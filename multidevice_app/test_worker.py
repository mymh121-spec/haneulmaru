"""Exercise real OS pipes without Rust, cameras, sensors or model weights."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from .contracts import Detection, Frame, SetupError
from .rust_host import RustEngine, WifiImu
from .tracking import RustTracking
from .worker import PipeWorker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.popen = subprocess.Popen
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "fixture.onnx").write_bytes(b"test-only")
        self.config = dict(model_path="fixture.onnx", input_size=32, confidence=.1, nms_iou=.7,
                           input_name="images", output_name="output0", startup_timeout_s=1,
                           request_timeout_s=1)

    def spawn(self, program):
        def factory(args, **kwargs):
            proc = self.popen([sys.executable, "-u", "-c", program], **kwargs)
            self.children.append(proc)
            self.addCleanup(self.cleanup, proc)
            return proc
        return patch("multidevice_app.worker.subprocess.Popen", side_effect=factory)

    @staticmethod
    def cleanup(proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=2)
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()

    def assert_reaped(self):
        for proc in self.children:
            self.assertIsNotNone(proc.poll())
            self.assertTrue(proc.stdin.closed)
            self.assertTrue(proc.stdout.closed)

    def engine(self):
        with patch("multidevice_app.rust_host.binary", return_value="fixture"):
            return RustEngine(self.config, self.root)

    def test_model_startup_timeout_reaps_child(self):
        with self.spawn("import time; time.sleep(30)"):
            start = time.monotonic()
            with self.assertRaisesRegex(SetupError, "model startup: response timeout"):
                self.engine()
            self.assertLess(time.monotonic() - start, 1.8)
        self.assert_reaped()

    def test_imu_startup_and_clock_sync_timeout_reap_child(self):
        for program, stage in (
            ("import time; time.sleep(30)", "IMU startup"),
            ("import time; print('{\"event\":\"ready\",\"protocol_version\":2,\"address\":\"127.0.0.1:1\"}'); time.sleep(30)", "clock sync"),
        ):
            with self.subTest(stage=stage), self.spawn(program), patch("multidevice_app.rust_host.binary", return_value="fixture"):
                hub = WifiImu(startup_timeout_s=1)
                with self.assertRaisesRegex(SetupError, stage):
                    hub.start()
                hub.stop()
        self.assert_reaped()

    def test_legacy_imu_worker_is_rejected_instead_of_refreshing_timestamps(self):
        with self.spawn("import time; print('{\"event\":\"ready\"}'); time.sleep(30)"), patch(
            "multidevice_app.rust_host.binary", return_value="fixture"
        ):
            with self.assertRaisesRegex(SetupError, "protocol_version=2"):
                WifiImu().start()
        self.assert_reaped()

    def test_frame_send_timeout_when_child_never_reads(self):
        with self.spawn("import sys,time; sys.stdin.buffer.readline(); print('ready'); time.sleep(30)"):
            engine = self.engine()
            start = time.monotonic()
            with self.assertRaisesRegex(SetupError, "frame inference: send timeout"):
                engine.detect(Frame(1, 0, np.zeros((1024, 1024, 3), np.uint8)))
            self.assertLess(time.monotonic() - start, 1.8)
            self.assertFalse(engine.worker.thread.is_alive())
            engine.close()
        self.assert_reaped()

    def test_frame_response_timeout_when_child_reads_then_hangs(self):
        program = ("import sys,struct,time; r=sys.stdin.buffer; r.readline(); print('ready'); "
                   "w,h=struct.unpack('<II',r.read(8)); r.read(w*h*3); time.sleep(30)")
        with self.spawn(program):
            engine = self.engine()
            start = time.monotonic()
            with self.assertRaisesRegex(SetupError, "frame inference: response timeout"):
                engine.detect(Frame(1, 0, np.zeros((32, 32, 3), np.uint8)))
            self.assertLess(time.monotonic() - start, 1.8)
            with self.assertRaisesRegex(SetupError, "closed"):
                engine.detect(Frame(1, 0, np.zeros((32, 32, 3), np.uint8)))
        self.assert_reaped()

    def test_empty_detection_is_success_but_bad_response_is_failure(self):
        for reply in ("[]", "{}", "not json", '[{"code":"R","confidence":2,"xyxy":[0,0,1,1]}]'):
            with self.subTest(reply=reply), self.spawn(
                "import sys,struct; r=sys.stdin.buffer; r.readline(); print('ready'); "
                "w,h=struct.unpack('<II',r.read(8)); r.read(w*h*3); print(" + repr(reply) + ")"
            ):
                engine = self.engine()
                frame = Frame(1, 0, np.zeros((32, 32, 3), np.uint8))
                if reply == "[]":
                    self.assertEqual(engine.detect(frame), [])
                    engine.close()
                else:
                    with self.assertRaisesRegex(SetupError, "invalid detection response"):
                        engine.detect(frame)
        self.assert_reaped()

    def test_tracking_timeout_and_invalid_ids_reset_worker(self):
        frame = Frame(1, 0, None)
        hits = [Detection("R", .9, (0, 0, 1, 1))] * 2
        for reply in (None, "[1, 1]", "[true, 2]", "[0, 2]", "garbage", "[1]"):
            program = "import sys,time; sys.stdin.buffer.readline(); "
            program += "time.sleep(30)" if reply is None else "print(" + repr(reply) + ")"
            with self.subTest(reply=reply), self.spawn(program), patch("multidevice_app.tracking.binary", return_value="fixture"):
                tracker = RustTracking(request_timeout_s=1)
                with self.assertRaises(SetupError):
                    tracker.update(frame, hits)
                self.assertIsNone(tracker.worker)
                tracker.close()
        self.assert_reaped()

    def test_pending_tracking_ids_are_allowed(self):
        with self.spawn("import sys; sys.stdin.buffer.readline(); print('[null, null]')"), patch(
            "multidevice_app.tracking.binary", return_value="fixture"
        ):
            tracker = RustTracking()
            hits = [Detection("R", .1, (0, 0, 1, 1))] * 2
            self.assertEqual([d.id for d in tracker.update(Frame(0, 0, None), hits)], [None, None])
            tracker.close()
        self.assert_reaped()

    def test_eof_is_an_error_and_close_is_idempotent(self):
        worker = PipeWorker([sys.executable, "-c", "pass"])
        self.addCleanup(worker.close)
        with self.assertRaisesRegex(SetupError, "failed; worker disconnected"):
            worker.exchange(b"hello\n", 1, "fixture")
        worker.close()
        self.assertIsNotNone(worker.proc.poll())


if __name__ == "__main__":
    unittest.main()
