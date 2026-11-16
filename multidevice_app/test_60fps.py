"""60fps correctness is enforced in CI; shared-runner speed is not an SLA."""
import unittest
import json
import numpy as np

from .verify_60fps import run
from .rust_host import ROOT, RustEngine
from .contracts import Frame


class SixtyFpsTests(unittest.TestCase):
    def test_recorded_60fps_motion_hold_and_imu_gap(self):
        report=run(count=600)
        self.assertTrue(report["functional_pass"])
        self.assertEqual(report["frames"],600)
        self.assertEqual(report["reference_512"]["tp"],2385)
        self.assertTrue(report["imu_expired_frames"])

    def test_isolated_decoder_preserves_60fps_timestamps_and_ids(self):
        report=run(count=600,isolated=True)
        self.assertTrue(report["functional_pass"])
        self.assertEqual(report["reference_512"]["id_switches"],0)

    def test_720p_binary_frames_do_not_create_padding_targets(self):
        report=run(count=90,width=1280,height=720,isolated=True)
        self.assertTrue(report["functional_pass"])
        self.assertLessEqual(report["object_counts"]["max"],4)

    def test_synthetic_model_ignores_achromatic_pixels_and_letterbox(self):
        config=json.loads((ROOT/"demo/config.json").read_text(encoding="utf-8"))["engine"]
        engine=RustEngine(config,ROOT/"demo")
        try:
            for level in (0,114,255):
                self.assertEqual(engine.detect(Frame(1,0,np.full((180,320,3),level,dtype=np.uint8))),[])
        finally:
            engine.close()
