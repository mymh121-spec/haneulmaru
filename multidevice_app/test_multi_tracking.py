"""Pixel-based synthetic integration test for the PC detection and tracking pipeline."""
import json
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from .contracts import Frame
from .imu import DirectionClassifier
from .inputs import OpenCVSource
from .pipeline import Pipeline
from .rust_host import ROOT, RustEngine
from .test_rust_host import attr, field, integer, io, node
from .tracking import create_tracker
from .model_profile import model_sha256

SIZE, FRAMES, STEP_MS = 128, 48, 50


def peak_test_graph():
    # Each local maximum produces a candidate centered at that pixel. The graph
    # receives only the RGB frame. It never receives the trajectory or track ID.
    count = SIZE * SIZE
    nodes = [
        node("MaxPool", ["images"], "max", [attr("kernel_shape", [7, 7]), attr("pads", [3]*4)]),
        node("Equal", ["images", "max"], "peaks", []),
        node("Cast", ["peaks"], "mask", [integer("to", 1)]),
        node("Mul", ["images", "mask"], "strength", []),
        node("Gather", ["strength", "order"], "rbg", [integer("axis", 1)]),
        node("Reshape", ["rbg", "shape"], "scores", []),
        node("Concat", ["boxes", "scores"], "output0", [integer("axis", 1)]),
    ]
    def tensor(name, dims, kind, data):
        return b"".join(field(1, d) for d in dims) + field(2, kind) + field(8, name) + field(9, data)
    y, x = np.indices((SIZE, SIZE))
    boxes = np.stack([x, y, np.full_like(x, 12), np.full_like(y, 12)]).astype("<f4")
    initializers = [tensor("boxes", [1, 4, count], 1, boxes.tobytes()),
                    tensor("order", [3], 7, struct.pack("<3q", 0, 2, 1)),
                    tensor("shape", [3], 7, struct.pack("<3q", 1, 3, count))]
    graph = (b"".join(field(1, n) for n in nodes) + field(2, "synthetic_peaks")
             + b"".join(field(5, value) for value in initializers)
             + field(11, io("images", [1, 3, SIZE, SIZE])) + field(12, io("output0", [1, 7, count])))
    return field(1, 7) + field(7, graph) + field(8, field(2, 11))


def targets(i):
    visible = {"east": ("R", 14 + 2*i, 38)}
    if not 21 <= i <= 25:
        visible["west"] = ("R", 108 - 2*i, 38)
    if not 12 <= i <= 15:
        visible["blue"] = ("B", 28 + i, 87)
    if i >= 6:
        visible["green"] = ("G", 102 - i, 106)
    return visible


def pixels(i):
    frame = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    y, x = np.indices(frame.shape[:2])
    for label, (color, cx, cy) in targets(i).items():
        # Changing intensities change detector confidence order on alternate frames.
        peak = (245 if i % 2 == 0 else 188) if label == "east" else 220
        signal = np.maximum(peak - 12*((x-cx)**2 + (y-cy)**2), 0).astype(np.uint8)
        plane = frame[:, :, {"B": 0, "G": 1, "R": 2}[color]]
        np.maximum(plane, signal, out=plane)
    return frame


def pipeline_for_clip(root):
    (root / "test-only.onnx").write_bytes(peak_test_graph())
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    engine_cfg = dict(config["engine"], model_path="test-only.onnx", input_size=SIZE)
    model = json.loads((ROOT / config["imu"]["training_path"]).read_text(encoding="utf-8"))
    return Pipeline(RustEngine(engine_cfg, root), create_tracker(config["tracking"]),
                    DirectionClassifier(model), config["color_confidence"], config["reset_tracking_after_gap_ms"])


class MultiObjectTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV is required for the AVI regression")
    def test_bundled_video_matches_all_reference_tracks(self):
        self.check_bundled_video(aliases=False)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV is required for the AVI regression")
    def test_display_aliases_preserve_all_reference_tracks(self):
        self.check_bundled_video(aliases=True)

    def check_bundled_video(self, aliases):
        demo = ROOT / "demo"
        config = json.loads((demo / "config.json").read_text(encoding="utf-8"))
        if aliases:
            config["engine"]["display_names"] = {"R": "빨강", "B": "파랑", "G": "초록"}
            config["engine"]["deployment"] = dict(
                model_sha256=model_sha256(demo / config["engine"]["model_path"]), classes=["R", "B", "G"])
        model = json.loads((demo / config["imu"]["training_path"]).read_text(encoding="utf-8"))
        pipeline = Pipeline(RustEngine(config["engine"], demo), create_tracker(config["tracking"]),
                            DirectionClassifier(model), config["color_confidence"],
                            config["reset_tracking_after_gap_ms"])
        self.addCleanup(pipeline.close)
        source = OpenCVSource(demo / "multi-tracking.avi", recording=True)
        self.addCleanup(source.close)
        expected = [json.loads(line) for line in (demo / "expected-tracks.jsonl").read_text().splitlines()]
        self.assertEqual(len(expected), 48)
        count, session = 0, None
        for reference in expected:
            frame = source.read()
            self.assertIsNotNone(frame)
            result = pipeline.process(frame, {}, attach_live_imu=False)
            self.assertEqual(result["frame_sequence"], reference["frame"])
            self.assertAlmostEqual(result["frame_timestamp_ms"], reference["time_ms"], places=4)
            actual = [dict(id=t["id"], code=t["code"], xyxy=list(t["xyxy"])) for t in result["tracks"]]
            self.assertEqual(actual, reference["tracks"], reference["frame"])
            self.assertEqual(result["engine"]["model_verification"]["status"], "verified" if aliases else "unverified")
            for track in result["tracks"]:
                self.assertEqual(track["display_name"], config["engine"].get("display_names", {}).get(track["code"], track["code"]))
            session = result["session_id"] if session is None else session
            self.assertEqual(result["session_id"], session)
            self.assertFalse(result["imu"]["valid"])
            count += len(actual)
        self.assertIsNone(source.read())
        self.assertEqual(count, 177)

    def test_recording_uses_video_time_instead_of_processing_speed(self):
        cv2 = Mock()
        capture = cv2.VideoCapture.return_value
        capture.read.return_value = (True, pixels(0))
        capture.get.side_effect = [0., 50., 50., 20.]
        with patch.dict("sys.modules", cv2=cv2):
            source = OpenCVSource("clip.avi", recording=True)
            try:
                frames = [source.read() for _ in range(3)]
            finally:
                source.close()
        self.assertEqual([(f.timestamp_ms, f.timestamp_basis) for f in frames],
                         [(0., "video_pts"), (50., "video_pts"), (100., "video_fps_estimate")])

    def test_crossing_and_occlusion_in_image_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = pipeline_for_clip(Path(directory))
            self.addCleanup(pipeline.close)
            identity = {}
            for i in range(FRAMES):
                frame = Frame(i, i * STEP_MS, pixels(i), "video_pts")
                result = pipeline.process(frame, {}, attach_live_imu=False)
                tracks = result["tracks"]
                expected = targets(i)
                self.assertEqual(len(tracks), len(expected), i)
                self.assertEqual(len({t["id"] for t in tracks}), len(tracks))
                for name, (color, x, y) in expected.items():
                    match = [t for t in tracks if t["xyxy"] == (x-6, y-6, x+6, y+6)]
                    self.assertEqual(len(match), 1, (i, name))
                    self.assertEqual(match[0]["code"], color)
                    self.assertIsInstance(match[0]["id"], int)
                    self.assertEqual(match[0]["id"], identity.setdefault(name, match[0]["id"]), (i, name))
            self.assertEqual(len(set(identity.values())), 4)
            # A large timestamp gap starts a new tracking session.
            last_session = result["session_id"]
            restarted = pipeline.process(Frame(FRAMES, 5000, pixels(0), "video_pts"), {}, attach_live_imu=False)
            self.assertNotEqual(restarted["session_id"], last_session)
