"""Native-only host integration tests; enable with HANEULMARU_NATIVE_VIDEO."""
import functools
import http.server
import json
import os
import socket
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from .rust_host import ROOT
from .model_profile import model_sha256
from .verify_native_video import command, run
from .verify_60fps import make_recording


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@unittest.skipUnless(os.environ.get("HANEULMARU_NATIVE_VIDEO"), "set HANEULMARU_NATIVE_VIDEO after building the native SDK")
class NativeVideoTests(unittest.TestCase):
    def invoke(self, source=None, *args, env=None):
        return subprocess.run(command(source or ROOT / "demo/multi-tracking.avi", *args),
                              cwd=ROOT.parent, capture_output=True, timeout=30, env=env)

    def packets(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return [json.loads(row) for row in result.stdout.splitlines()]

    def config(self, directory, **changes):
        cfg = json.loads((ROOT / "demo/config.json").read_text())
        cfg["engine"]["model_path"] = str(ROOT / "demo/synthetic-peaks.onnx")
        cfg.update(changes)
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_600_frames_at_60hz_match_reference(self):
        report = run()
        self.assertEqual(report["reference_512"]["tp"], 2385)

    def test_1080p_max_fps_60_preserves_frames(self):
        report = run(60, 1920, 1080, paced=True)
        # Performance is reported, never a timing gate on shared CI machines.
        self.assertTrue(report["functional_pass"])

    def test_ranges_repeats_and_custom_profile(self):
        result = self.invoke(None, "--start-ms", 80, "--end-ms", 240, "--repeat", 2,
                             "--profile", ROOT / "demo/profile.json")
        packets = self.packets(result)
        self.assertEqual([p["frame_sequence"] for p in packets], [3, 4, 5] * 2)
        self.assertEqual([p["replay_pass"] for p in packets], [0]*3 + [1]*3)
        self.assertNotEqual(packets[0]["session_id"], packets[3]["session_id"])
        self.assertEqual(packets[0]["tracks"], packets[3]["tracks"])
        self.assertTrue(all(t["display_name"] for p in packets for t in p["tracks"]))

    def test_rotation_swaps_nonsquare_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = make_recording(temp, 48, 640, 360)
            config = self.config(temp, camera=dict(live=False, source="", rotation_clockwise_deg=90))
            packets = self.packets(self.invoke(clip, "--config", config, "--max-frames", 2))
            self.assertTrue(all(p["image_size_px"] == [360, 640] for p in packets))

    def test_deployment_hash_and_class_order_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = json.loads((ROOT / "demo/profile.json").read_text(encoding="utf-8"))
            profile["model_path"] = str(ROOT / "demo/synthetic-peaks.onnx")
            profile["deployment"] = dict(model_sha256=model_sha256(Path(profile["model_path"])), classes=profile["classes"][:])
            path = Path(temp) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            packets = self.packets(self.invoke(None, "--profile", path, "--max-frames", 1))
            self.assertEqual(packets[0]["engine"]["model_verification"]["status"], "verified")
            profile["deployment"]["classes"].reverse()
            path.write_text(json.dumps(profile), encoding="utf-8")
            self.assertNotEqual(self.invoke(None, "--profile", path, "--max-frames", 1).returncode, 0)
            profile["deployment"]["classes"].reverse()
            profile["deployment"]["model_sha256"] = "0" * 64
            path.write_text(json.dumps(profile), encoding="utf-8")
            self.assertIn(b"SHA-256 mismatch", self.invoke(None, "--profile", path, "--max-frames", 1).stderr)

    def test_invalid_input_and_existing_output_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertNotEqual(self.invoke(Path(temp)/"missing.avi").returncode, 0)
            out = Path(temp) / "existing.jsonl"
            out.write_bytes(b"preserve me")
            self.assertNotEqual(self.invoke(None, "--jsonl", out).returncode, 0)
            self.assertEqual(out.read_bytes(), b"preserve me")
            self.assertNotEqual(self.invoke(None, "--max-fps", "NaN").returncode, 0)
            self.assertIn(b"max-fps must be", self.invoke(None, "--max-fps", "1e-200").stderr)
            self.assertNotEqual(self.invoke(None, "--start-ms", 10000).returncode, 0)

    def test_live_eof_reconnects_with_new_session_and_finite_budget(self):
        handler = functools.partial(QuietHandler, directory=str(ROOT / "demo"))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = self.config(temp, camera=dict(live=True, source="", rotation_clockwise_deg=0))
                url = f"http://127.0.0.1:{server.server_port}/multi-tracking.avi"
                packets = self.packets(self.invoke(url, "--config", config, "--max-frames", 60, "--max-restarts", 1))
                self.assertEqual([p["frame_sequence"] for p in packets], list(range(1, 61)))
                self.assertEqual(len({p["session_id"] for p in packets}), 2)
                self.assertEqual(packets[-1]["recovery_count"], 1)
                self.assertTrue(all(a["frame_timestamp_ms"] < b["frame_timestamp_ms"] for a, b in zip(packets, packets[1:])))
                failed = self.invoke(url, "--config", config, "--max-frames", 120, "--max-restarts", 1)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(len(failed.stdout.splitlines()), 96)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_stalled_pipeline_start_and_frame_obey_deadlines_and_retry_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / ("stall.exe" if os.name == "nt" else "stall")
            subprocess.run(["rustc", str(ROOT / "native_video/tests/stall.rs"), "-o", str(fake)], check=True, timeout=60)
            config = self.config(temp)
            cfg = json.loads(config.read_text())
            cfg["engine"].update(startup_timeout_s=.5, request_timeout_s=.5)
            config.write_text(json.dumps(cfg), encoding="utf-8")
            for mode in ("startup", "frame"):
                marker = root / f"{mode}.txt"
                env = dict(os.environ, NATIVE_STALL_MODE=mode, NATIVE_STALL_MARKER=str(marker))
                start = time.perf_counter()
                result = self.invoke(None, "--config", config, "--worker", fake, "--max-restarts", 1, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"deadline", result.stderr)
                self.assertEqual(marker.read_text().splitlines(), ["start", "start"])
                self.assertLess(time.perf_counter()-start, 5)

    def test_live_imu_delivery_and_expiry_without_hardware(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "demo")))
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        stop = threading.Event()
        def send_samples():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                seq = 0
                while not stop.is_set():
                    seq += 1
                    packet = dict(key="native-test-key", session="native-test", seq=seq,
                                  packet=dict(v=1, t_ms=seq*20, ax=0, ay=0, az=1, gx=-180, gy=-12, gz=-11))
                    sender.sendto(json.dumps(packet).encode(), ("127.0.0.1", port))
                    stop.wait(.02)
        sending = threading.Thread(target=send_samples, daemon=True)
        sending.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = self.config(temp, camera=dict(live=True, source="", rotation_clockwise_deg=0),
                                     imu=dict(enabled=True, bind=f"127.0.0.1:{port}", training_path=str(ROOT / "assets/nu40-imu-direction-training.json")))
                url = f"http://127.0.0.1:{server.server_port}/multi-tracking.avi"
                with subprocess.Popen(command(url, "--config", config, "--max-frames", 40, "--max-fps", 20),
                                      cwd=ROOT.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=dict(os.environ, HANEULMARU_PAIR_KEY="native-test-key")) as child:
                    timer = threading.Timer(20, child.kill)
                    timer.start()
                    packets = []
                    try:
                        for line in child.stdout:
                            packets.append(json.loads(line))
                            if len(packets) == 15:
                                stop.set()
                        self.assertEqual(child.wait(), 0, child.stderr.read().decode(errors="replace"))
                    finally:
                        timer.cancel()
                        if child.poll() is None:
                            child.kill()
                self.assertEqual(len(packets), 40)
                self.assertTrue(any(p["imu"]["valid"] and p["imu"]["direction"] == "up" for p in packets[:15]))
                self.assertTrue(all(not p["imu"]["valid"] for p in packets[-10:]))
        finally:
            stop.set()
            sending.join()
            server.shutdown()
            server.server_close()
            serving.join()
