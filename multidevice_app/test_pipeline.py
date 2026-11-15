from dataclasses import replace
import unittest
from unittest.mock import Mock

import numpy as np

from .contracts import Detection, Frame, SetupError
from .pipeline import Pipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.hit = Detection("R", .9, (1, 2, 5, 6))
        self.engine = Mock(name="engine", ready=True, reason=None)
        self.engine.detect.return_value = [self.hit]
        self.tracker = Mock()
        self.tracker.update.side_effect = lambda frame, hits: hits
        self.pipeline = Pipeline(self.engine, self.tracker, Mock())
        self.frame = Frame(1, 10, np.zeros((32, 32, 3), np.uint8))

    def process(self, frame=None):
        return self.pipeline.process(frame or self.frame, {}, attach_live_imu=False)

    def test_detection_failure_can_retry_same_frame_without_reset(self):
        session = self.pipeline.session
        self.engine.detect.side_effect = [SetupError("one-off failure"), [self.hit]]
        with self.assertRaises(SetupError):
            self.process()
        self.assertIsNone(self.pipeline.previous)
        self.tracker.update.assert_not_called()
        result = self.process()
        self.assertEqual(result["session_id"], session)
        self.tracker.reset.assert_not_called()
        with self.assertRaisesRegex(ValueError, "ordered"):
            self.process()
        self.assertEqual(self.engine.detect.call_count, 2)

    def test_lost_tracker_reply_retries_in_new_session(self):
        first = self.process()
        state = [1]
        def uncertain(frame, hits):
            state[0] += 1
            raise SetupError("response lost after applying observation")
        self.tracker.update.side_effect = uncertain
        next_frame = replace(self.frame, sequence=2, timestamp_ms=20)
        with self.assertRaises(SetupError):
            self.process(next_frame)
        self.assertEqual(self.pipeline.previous[0], 1)
        self.tracker.reset.side_effect = lambda: state.__setitem__(0, 0)
        def successful(frame, hits):
            state[0] += 1
            return [replace(hits[0], id=state[0])]
        self.tracker.update.side_effect = successful
        retried = self.process(next_frame)
        self.assertNotEqual(retried["session_id"], first["session_id"])
        self.assertEqual(retried["tracks"][0]["id"], 1)
        self.tracker.reset.assert_called_once()

    def test_invalid_tracker_output_also_requires_new_session(self):
        session = self.pipeline.session
        self.tracker.update.side_effect = None
        self.tracker.update.return_value = [replace(self.hit, xyxy=(0, 0, 99, 99))]
        with self.assertRaises(ValueError):
            self.process()
        self.tracker.update.return_value = [self.hit]
        self.assertNotEqual(self.process()["session_id"], session)
        self.tracker.reset.assert_called_once()

    def test_failed_reset_cannot_resume_uncertain_tracker(self):
        self.tracker.update.side_effect = SetupError("lost reply")
        with self.assertRaises(SetupError):
            self.process()
        self.tracker.reset.side_effect = [SetupError("reset failed"), None]
        self.tracker.update.side_effect = lambda frame, hits: hits
        with self.assertRaises(SetupError):
            self.process()
        self.assertTrue(self.pipeline.tracking_uncertain)
        self.process()
        self.assertEqual(self.tracker.reset.call_count, 2)

    def test_gap_backward_time_and_resolution_change_reset_session(self):
        for changed in (dict(timestamp_ms=5000), dict(timestamp_ms=0),
                        dict(image=np.zeros((48, 48, 3), np.uint8))):
            with self.subTest(changed=list(changed)):
                self.setUp()
                first = self.process()
                second = self.process(replace(self.frame, sequence=2, **changed))
                self.assertNotEqual(first["session_id"], second["session_id"])
                self.tracker.reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
