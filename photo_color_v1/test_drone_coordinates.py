"""Direction geometry and IMU continuity, without GPU/camera/USB access."""
import math
import unittest
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from drone_coordinates import GyroPose, NorthCoordinateSession


MODEL = {"format": "nu40-imu-direction-model", "units": "degrees_per_second",
         "directions": {"up": [-1, 0, 0], "down": [1, 0, 0], "left": [0, 1, 0], "right": [0, -1, 0]}}


def pose(heading=0, pitch=0, roll=0, valid=True):
    return dict(heading_deg=heading, pitch_deg=pitch, roll_deg=roll, valid=valid)


def sample(stamp, gyro=(0, -90, 0), source="USB", session="a"):
    return dict(source=source, session=session, age_ms=0,
                sample=dict(t_ms=stamp, gx=gyro[0], gy=gyro[1], gz=gyro[2]))


class CoordinatesTest(unittest.TestCase):
    def center(self, session, camera_pose):
        return session.record([dict(id=1, code="R", xyxy=[310, 230, 330, 250])], 640, 480, camera_pose)["observations"][0]

    def test_north_then_east_south_west_without_distance(self):
        s = NorthCoordinateSession()
        for heading, expected in [(123, [0, 1, 0]), (213, [1, 0, 0]), (303, [0, -1, 0]), (393, [-1, 0, 0])]:
            o = self.center(s, pose(heading))
            for actual, target in zip(o["direction_enu_unit"], expected):
                self.assertAlmostEqual(actual, target)
            self.assertIsNone(o["position_enu_m"])
            self.assertIsNone(o["range_m"])

    def test_pixels_move_east_and_up_and_scale_consistently(self):
        s = NorthCoordinateSession()
        a = s.record([dict(xyxy=[400, 100, 400, 100])], 640, 480, pose())
        b = s.record([dict(xyxy=[800, 200, 800, 200])], 1280, 960, pose())
        o = a["observations"][0]
        self.assertGreater(o["direction_enu_unit"][0], 0)
        self.assertGreater(o["direction_enu_unit"][2], 0)
        self.assertAlmostEqual(math.hypot(*o["direction_enu_unit"]), 1)
        self.assertEqual(o["direction_enu_unit"], b["observations"][0]["direction_enu_unit"])
        self.assertFalse(a["projection"]["calibrated"])

    def test_stationary_drone_keeps_north_when_camera_rotates_east(self):
        s = NorthCoordinateSession({"vertical_fov_deg": 90})
        s.record([], 640, 640, pose(0))
        # With a square 90-degree view, a drone 45 degrees left of the new
        # camera heading is on the left edge. Its world direction stays north.
        o = s.record([dict(xyxy=[0, 320, 0, 320])], 640, 640, pose(45))["observations"][0]
        for actual, target in zip(o["direction_enu_unit"], [0, 1, 0]):
            self.assertAlmostEqual(actual, target)
        self.assertAlmostEqual(o["bearing_deg"], 0)

    def test_roll_and_vertical_bearing(self):
        s = NorthCoordinateSession()
        o = s.record([dict(xyxy=[400, 240, 400, 240])], 640, 480, pose(roll=90))["observations"][0]
        self.assertLess(o["elevation_deg"], 0)
        up = self.center(s, pose(pitch=90))
        self.assertIsNone(up["bearing_deg"])
        self.assertAlmostEqual(up["elevation_deg"], 90)

    def test_missing_pose_does_not_set_north_or_fake_coordinates(self):
        s = NorthCoordinateSession()
        o = self.center(s, pose(70, valid=False))
        self.assertIsNone(o["direction_enu_unit"])
        self.assertIsNone(s.initial_heading)
        self.assertAlmostEqual(self.center(s, pose(240))["bearing_deg"], 0)

    def test_bad_boxes_and_intrinsics_rejected(self):
        with self.assertRaises(ValueError):
            NorthCoordinateSession().record([dict(xyxy=[-1, 0, 10, 10])], 640, 480, pose())
        with self.assertRaises(ValueError):
            NorthCoordinateSession({"intrinsics": {"fx": -1}}).record([], 640, 480, pose())

    def test_slow_motion_not_gated_by_gesture_threshold(self):
        p = GyroPose(MODEL)
        p.consume(sample(0, (0, -1, 0)))
        self.assertAlmostEqual(p.consume(sample(100, (0, -1, 0)))["heading_deg"], .1)

    def test_transport_switch_old_samples_rollover_and_restart(self):
        p = GyroPose(MODEL)
        p.consume(sample(0xfffffff0))
        p.consume(sample(34, source="BLE"))
        self.assertAlmostEqual(p.snapshot()["heading_deg"], 4.5)
        p.consume(sample(30))
        self.assertAlmostEqual(p.snapshot()["heading_deg"], 4.5)
        p.consume(sample(134))
        self.assertAlmostEqual(p.snapshot()["heading_deg"], 13.5)
        self.assertFalse(p.consume(sample(800))["valid"])
        self.assertFalse(p.consume(sample(900))["valid"])
        p.reset()
        self.assertTrue(p.consume(sample(1000))["valid"])
        p.consume(sample(2000))
        self.assertFalse(p.consume(sample(100))["valid"])
        p.reset()
        p.consume(sample(150))
        self.assertFalse(p.consume(sample(10))["valid"])

    def test_cli_writes_headless_coordinates_and_preserves_legacy_mode(self):
        import track_yolo_rgb
        frame = SimpleNamespace(shape=(480, 640, 3))
        tracks = [dict(id=7, code="B", xyxy=[310, 230, 330, 250])]
        for legacy in (False, True):
            cap = Mock()
            cap.isOpened.return_value = True
            cap.get.return_value = 30
            cap.read.side_effect = [(True, frame), (False, None)]
            cv2 = SimpleNamespace(VideoCapture=Mock(return_value=cap), CAP_PROP_FPS=1)
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "result.jsonl"
                args = ["track_yolo_rgb.py", "--weights", "future-best.pt", "--source", "0", "--jsonl", str(output)]
                if legacy:
                    args.append("--no-system-coordinates")
                with patch.object(sys, "argv", args), patch.dict(sys.modules, {"cv2": cv2}), patch.object(track_yolo_rgb, "RGBTracker") as tracker:
                    tracker.return_value.update.return_value = tracks
                    track_yolo_rgb.main()
                packet = json.loads(output.read_text())
                self.assertEqual(packet["tracks"], tracks)
                if legacy:
                    self.assertEqual(set(packet), {"frame", "tracks"})
                else:
                    observation = packet["system_coordinates"]["observations"][0]
                    self.assertEqual(observation["id"], 7)
                    self.assertAlmostEqual(observation["bearing_deg"], 0)
                    self.assertEqual(observation["direction_enu_unit"], [0, 1, 0])
                    self.assertIsNone(observation["position_enu_m"])
                cap.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
