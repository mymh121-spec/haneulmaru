import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from .contracts import Detection, Frame, SetupError
from .coordinates import CoordinateAdapter
from .dataset_quality import audit
from .datasets import Sample, write_dataset
from .deployment import bundle, verify_bundle
from .evaluation import evaluate, match
from .recovery import Budget, RecoveringPipeline, RecoveringSource
from .replay import CachingEngine, DetectionReplay, ImuReplay
from .review_report import write_report
from .rust_host import ROOT


def hit(identity=1, code="R", box=(0, 0, 10, 10)):
    return dict(id=identity, code=code, xyxy=list(box), confidence=.9)


def packet(number, tracks, session="s", **kwargs):
    return dict(frame_sequence=number, frame_timestamp_ms=number*50, image_size_px=[20, 20],
                session_id=session, tracks=tracks, **kwargs)


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def jsonl(self, name, rows):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path


class ReplayTests(TemporaryTest):
    def event(self, stamp, arrival=None, session="a"):
        return dict(time_ms=stamp, available_ms=stamp if arrival is None else arrival, session=session,
                    sample=dict(t_ms=int(stamp), gx=0, gy=10, gz=0))

    def test_delay_staleness_seek_and_no_future_measurement(self):
        replay = ImuReplay(self.jsonl("imu.jsonl", [self.event(0), self.event(100, 800), self.event(200)]))
        self.assertEqual(replay.snapshot_at(50)["sample"]["t_ms"], 0)
        self.assertEqual(replay.snapshot_at(250)["sample"]["t_ms"], 200)
        self.assertIsNone(replay.snapshot_at(800)["sample"])
        self.assertEqual(replay.snapshot_at(250)["sample"]["t_ms"], 200)
        self.assertEqual(replay.snapshot_at(50)["sample"]["t_ms"], 0)

    def test_offset_and_invalid_records(self):
        path = self.jsonl("imu.jsonl", [self.event(0)])
        replay = ImuReplay(path, offset_ms=100)
        self.assertIsNone(replay.snapshot_at(99)["sample"])
        self.assertEqual(replay.snapshot_at(100)["age_ms"], 0)
        for row in (self.event(1, 0), dict(self.event(0), time_ms=float("nan")), dict(time_ms=1), self.event(0, session="")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                ImuReplay(self.jsonl("bad.jsonl", [row]))
        with self.assertRaises(ValueError):
            ImuReplay(self.jsonl("duplicates.jsonl", [self.event(0), self.event(0)]))

    def test_detection_cache_roundtrip_and_video_mismatch(self):
        frame = Frame(1, 50, np.zeros((20, 20, 3), np.uint8))
        engine = Mock()
        engine.model_verification, engine.display_names = {"status": "unverified"}, {}
        engine.detect.return_value = [Detection("R", .9, (0, 0, 10, 10))]
        path = self.root / "detections.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            CachingEngine(engine, stream).detect(frame)
        replay = DetectionReplay(path)
        self.assertEqual(replay.detect(frame), engine.detect.return_value)
        with self.assertRaises(SetupError):
            replay.detect(Frame(1, 51, frame.image))
        with self.assertRaises(SetupError):
            replay.detect(Frame(2, 50, frame.image))
        with self.assertRaisesRegex(SetupError, "pixels"):
            replay.detect(Frame(1, 50, np.ones_like(frame.image)))

    def test_recorded_imu_coordinates_and_gap_uncertainty(self):
        model = json.loads((ROOT / "assets/nu40-imu-direction-training.json").read_text(encoding="utf-8"))
        adapter = CoordinateAdapter(model, {"vertical_fov_deg": 55})
        state = dict(source="recorded_imu", session="a", age_ms=0, sample=dict(t_ms=0, gx=0, gy=10, gz=0))
        result = adapter.record(dict(packet(1, [hit()]), timestamp_basis="video_pts"), state)
        self.assertTrue(result["camera_pose"]["valid"])
        self.assertIsNone(result["observations"][0]["range_m"])
        stale = dict(state, age_ms=500)
        result = adapter.record(dict(packet(2, [hit()]), timestamp_basis="video_pts"), stale)
        self.assertIsNone(result["observations"][0]["direction_enu_unit"])
        result = adapter.record(dict(packet(3, [hit()], session="new"), timestamp_basis="video_pts"), state)
        self.assertTrue(result["camera_pose"]["valid"])


class EvaluationTests(unittest.TestCase):
    def test_misaligned_frame_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
            evaluate([packet(1, [])], [dict(packet(1, []), frame_timestamp_ms=51)])

    def test_global_matching_avoids_greedy_miss(self):
        a = [hit(1, box=(0, 0, 10, 10)), hit(2, box=(4, 0, 14, 10))]
        b = [hit(10, box=(2, 0, 12, 10)), hit(20, box=(0, 0, 6, 10))]
        self.assertEqual({(r, p) for r, p, _ in match(a, b, .5)}, {(0, 1), (1, 0)})

    def test_missing_extra_class_mismatch_and_session_id_switch(self):
        reference = [packet(i, [hit()]) for i in (1, 2, 3)]
        predictions = [packet(1, [hit(99)]), packet(2, [hit(99)], session="reset"), packet(4, [hit()])]
        report = evaluate(reference, predictions)
        self.assertEqual([report["totals"][k] for k in ("tp", "fp", "fn", "id_switches")], [2, 1, 1, 1])
        self.assertEqual(report["missing_frames"], 1)
        wrong = evaluate([packet(1, [hit()])], [packet(1, [hit(code="B")])])
        self.assertEqual(wrong["totals"]["tp"], 0)
        self.assertEqual(wrong["totals"]["fn"], 1)

    def test_pending_ids_and_timing_warmup(self):
        actual = [packet(i, [hit()]) for i in range(3)]
        predicted = [packet(i, [hit(None)], engine=dict(inference_ms=t), processing_ms=t+1)
                     for i, t in enumerate((100, 2, 4))]
        report = evaluate(actual, predicted, warmup=1)
        self.assertEqual(report["totals"]["matched_without_id"], 3)
        self.assertEqual(report["totals"]["id_switches"], 0)
        self.assertEqual(report["timing"]["inference"]["mean_ms"], 3)
        self.assertEqual(report["timing"]["inference"]["p95_ms"], 4)

    def test_empty_predictions_count_all_misses_and_invalid_records_fail(self):
        self.assertEqual(evaluate([packet(1, [hit()])], [])["totals"]["fn"], 1)
        for rows in ([packet(1, [hit(), hit()])], [packet(1, []), packet(1, [])], [packet(1, [hit(box=(2, 0, 1, 1))])]):
            with self.assertRaises(ValueError):
                evaluate([packet(1, [])], rows)


class RecoveryTests(unittest.TestCase):
    def test_pipeline_retry_replaces_uncertain_state_and_stops_at_budget(self):
        failed, recovered = Mock(), Mock()
        failed.process.side_effect = SetupError("lost reply")
        recovered.process.return_value = dict(session_id="new", tracks=[])
        factory = Mock(side_effect=[failed, recovered])
        runner = RecoveringPipeline(factory, Budget(1, 0))
        self.assertEqual(runner.process(None)["session_id"], "new")
        failed.close.assert_called_once()
        recovered.process.side_effect = SetupError("again")
        with self.assertRaises(SetupError):
            runner.process(None)
        self.assertEqual(factory.call_count, 2)
        runner.close()

    def test_source_reconnects_preserving_sequence_but_eof_is_final_for_files(self):
        a, b = Mock(), Mock()
        frame = Frame(1, 0, None)
        a.read.side_effect = [frame, None]
        b.read.return_value = frame
        source = RecoveringSource(Mock(side_effect=[a, b]), Budget(1, 0), live=True)
        self.assertEqual(source.read().sequence, 1)
        self.assertEqual(source.read().sequence, 2)
        self.assertEqual(source.generation, 1)
        source.close()
        factory = Mock(return_value=Mock(read=Mock(return_value=None)))
        source = RecoveringSource(factory, Budget(3, 0))
        self.assertIsNone(source.read())
        self.assertEqual(factory.call_count, 1)
        source.close()


class QualityTests(TemporaryTest):
    def test_reencoded_identical_pixels_are_rejected_atomically(self):
        a, b = self.root/"a.png", self.root/"b.png"
        image = Image.new("RGB", (20, 10), "red")
        image.save(a, compress_level=0)
        image.save(b, compress_level=9)
        self.assertNotEqual(a.read_bytes(), b.read_bytes())
        with self.assertRaisesRegex(ValueError, "Duplicate image pixels"):
            write_dataset(self.root/"output", ["R"], {"train": [Sample(a, [(0,.5,.5,.2,.2)])],
                                                       "val": [Sample(b, [])]})
        self.assertFalse((self.root/"output").exists())
        self.assertEqual(list(self.root.glob(".dataset-*")), [])

    def test_audit_duplicate_group_distribution_and_bounded_candidates(self):
        import yaml
        for name in ("train", "val"):
            (self.root/name).mkdir()
        for folder, suffix, color in (("train", "a", "red"), ("train", "b", "blue"), ("val", "a", "red")):
            Image.new("RGB", (20, 10), color).save(self.root/folder/(suffix+".png"))
            (self.root/folder/(suffix+".txt")).write_text("0 .5 .5 .2 .2\n")
        data = self.root/"data.yaml"
        data.write_text(yaml.safe_dump(dict(names=["object", "absent"], train="train", val="val")))
        report = audit(data, {"train/a.png":"clip1", "val/a.png":"clip1"}, max_comparisons=1)
        self.assertEqual(report["group_leakage"], {"clip1": ["train", "val"]})
        self.assertTrue(report["exact_pixel_duplicates"][0]["cross_split"])
        self.assertEqual(report["missing_classes"]["train"], ["absent"])
        self.assertTrue(report["search_truncated"])


class BundleAndReportTests(TemporaryTest):
    def test_bundle_portable_verification_tampering_and_output_protection(self):
        model = self.root/"model.onnx"
        model.write_bytes(b"synthetic model fixture")
        profile = self.root/"input.json"
        profile.write_text(json.dumps(dict(schema_version=1, classes=["object"], output_layout="yolo_raw",
                                           input_size=32, model_path="model.onnx")))
        classes = self.root/"classes.json"
        classes.write_text('["object"]')
        output = self.root/"release"
        evidence = self.root/"evaluation.json"
        import hashlib
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        evidence.write_text(json.dumps(dict(metric="class_aware_iou_assignment", model_hashes=[digest])))
        manifest = bundle(profile, classes, output, evaluation=evidence)
        self.assertEqual(manifest["evidence"]["evaluation_model_match"], "verified")
        self.assertEqual(verify_bundle(output)["status"], "verified")
        moved = self.root/"moved"
        output.rename(moved)
        self.assertEqual(verify_bundle(moved)["status"], "verified")
        with self.assertRaises(FileExistsError):
            bundle(profile, classes, moved)
        (moved/"model.onnx").write_bytes(b"tampered")
        with self.assertRaisesRegex(SetupError, "checksum"):
            verify_bundle(moved)
        evidence.write_text(json.dumps(dict(metric="class_aware_iou_assignment", model_hashes=["0"*64])))
        with self.assertRaisesRegex(ValueError, "different model"):
            bundle(profile, classes, self.root/"wrong", evaluation=evidence)
        self.assertFalse((self.root/"wrong").exists())
        self.assertEqual(list(self.root.glob(".bundle-*")), [])

    def test_html_report_escapes_untrusted_labels_and_preserves_output(self):
        source = self.jsonl("frames.jsonl", [packet(1, [hit(code='</script><script>alert(1)</script>')])])
        output = self.root/"review.html"
        write_report(source, output)
        html = output.read_text(encoding="utf-8")
        self.assertNotIn('</script><script>alert(1)', html)
        self.assertIn('\\u003c/script\\u003e', html)
        self.assertIn('id="seek"', html)
        with self.assertRaises(FileExistsError):
            write_report(source, output)


class OfflineIntegrationTests(TemporaryTest):
    def command(self, module, *args):
        result = subprocess.run([sys.executable, "-m", module, *map(str, args)],
                                cwd=ROOT.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_cli_cache_sensor_repeat_coordinates_evaluation_and_report(self):
        cache, output, repeated = (self.root/name for name in ("cache.jsonl", "out.jsonl", "repeat.jsonl"))
        imu = self.jsonl("imu.jsonl", [dict(time_ms=i*50, session="fixture",
            sample=dict(t_ms=i*50, gx=0, gy=10, gz=0)) for i in range(48)])
        projection = self.root/"projection.json"
        projection.write_text('{"vertical_fov_deg":55}')
        base = ["--config", ROOT/"demo/config.json", "--source", ROOT/"demo/multi-tracking.avi", "--recording"]
        self.command("multidevice_app.run", *base, "--imu-log", imu, "--coordinates", projection,
                     "--cache-detections", cache, "--jsonl", output, "--flush-every", 16)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 48)
        self.assertTrue(all(row["pipeline_backend"] == "rust" for row in rows))
        self.assertTrue(all(row["imu"]["valid"] for row in rows))
        self.assertTrue(all(row["coordinates"]["camera_pose"]["valid"] for row in rows))
        evaluation = self.root/"evaluation.json"
        self.command("multidevice_app.offline", "evaluate", "--reference", ROOT/"demo/expected-tracks.jsonl",
                     "--predictions", output, "--output", evaluation)
        report = json.loads(evaluation.read_text(encoding="utf-8"))
        self.assertEqual([report["totals"][k] for k in ("tp", "fp", "fn", "id_switches")], [177, 0, 0, 0])
        self.command("multidevice_app.run", *base, "--detections", cache, "--no-imu",
                     "--start-ms", 500, "--end-ms", 1000, "--repeat", 2, "--jsonl", repeated)
        replayed = [json.loads(line) for line in repeated.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(replayed), 20)
        self.assertEqual([row["frame_sequence"] for row in replayed], list(range(11,21))*2)
        self.assertEqual(len({row["session_id"] for row in replayed}), 2)
        self.assertEqual([row["tracks"] for row in replayed[:10]], [row["tracks"] for row in replayed[10:]])
        html = self.root/"review.html"
        self.command("multidevice_app.offline", "report", "--input", output, "--output", html)
        self.assertIn('id="seek"', html.read_text(encoding="utf-8"))

    def test_isolated_decoder_matches_direct_video_pixels_and_timestamps(self):
        from .inputs import OpenCVSource
        from .video_worker import IsolatedSource
        path = ROOT/"demo/multi-tracking.avi"
        direct = OpenCVSource(path, recording=True)
        self.addCleanup(direct.close)
        isolated = IsolatedSource(path, recording=True, timeout_s=10)
        self.addCleanup(isolated.close)
        for _ in range(48):
            a, b = direct.read(), isolated.read()
            self.assertEqual((a.sequence, a.timestamp_ms, a.timestamp_basis), (b.sequence, b.timestamp_ms, b.timestamp_basis))
            np.testing.assert_array_equal(a.image, b.image)
        self.assertIsNone(isolated.read())
