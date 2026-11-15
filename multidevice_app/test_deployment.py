import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .contracts import Detection, Frame, SetupError
from .model_profile import load_profile, seal_profile, verify_model
from .rust_host import RustEngine
from .tracking import create_tracker


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.weights = self.root / "weights.onnx"
        self.weights.write_bytes(b"test model bytes")
        self.profile = dict(schema_version=1, model_path="weights.onnx", input_size=32,
                            classes=["crate", "cart"], output_layout="yolo_raw",
                            display_names={"crate": "상자"}, class_change_policy="strict")
        self.path = self.root / "profile.json"
        self.path.write_text(json.dumps(self.profile), encoding="utf-8")

    def seal(self, classes=None):
        training = self.root / "classes.json"
        training.write_text(json.dumps(classes or self.profile["classes"]), encoding="utf-8")
        folder = self.root / "release"
        folder.mkdir(exist_ok=True)
        output = folder / "deployment.json"
        seal_profile(self.path, training, output)
        return output

    def test_seal_preserves_model_location_policy_and_aliases(self):
        path = self.seal()
        cfg = load_profile(path)
        self.assertEqual(Path(cfg["model_path"]).resolve(), self.weights)
        self.assertEqual(cfg["display_names"], {"crate": "상자"})
        self.assertEqual(cfg["class_change_policy"], "strict")
        self.assertEqual(verify_model(cfg, self.weights)["status"], "verified")
        with self.assertRaises(FileExistsError):
            self.seal()

    def test_training_class_order_cannot_be_replaced_by_profile_order(self):
        with self.assertRaisesRegex(SetupError, "클래스 이름/순서"):
            self.seal(["cart", "crate"])
        self.assertFalse((self.root / "release" / "deployment.json").exists())

    def test_changed_classes_and_weights_are_rejected_before_worker_start(self):
        cfg = dict(load_profile(self.seal()), confidence=.1, nms_iou=.7)
        for change in (dict(classes=["cart", "crate"]), dict(model_path=str(self.root / "other.onnx"))):
            (self.root / "other.onnx").write_bytes(b"different model")
            with self.subTest(change=change), patch("multidevice_app.rust_host.PipeWorker") as worker:
                with self.assertRaises(SetupError):
                    RustEngine(dict(cfg, **change), self.root)
                worker.assert_not_called()
        self.weights.write_bytes(b"changed after loading profile")
        with patch("multidevice_app.rust_host.PipeWorker") as worker:
            with self.assertRaisesRegex(SetupError, "SHA-256"):
                RustEngine(cfg, self.root)
            worker.assert_not_called()

    def test_legacy_profile_is_explicitly_unverified(self):
        cfg = load_profile(self.path)
        self.assertEqual(verify_model(cfg, self.weights),
                         dict(status="unverified", reason="missing_deployment_record"))

    def test_invalid_deployment_aliases_and_policy_are_rejected(self):
        invalid = [dict(deployment=None), dict(deployment={}),
                   dict(deployment=dict(model_sha256="z"*64, classes=self.profile["classes"])),
                   dict(display_names={"missing": "상자"}), dict(display_names={"crate": "\n"}),
                   dict(class_change_policy="guess")]
        for change in invalid:
            with self.subTest(change=change):
                self.path.write_text(json.dumps(dict(self.profile, **change)), encoding="utf-8")
                with self.assertRaises(SetupError):
                    load_profile(self.path)

    def test_strict_policy_is_not_silently_ignored_by_other_trackers(self):
        for name in ("bytetrack", "botsort"):
            with self.assertRaisesRegex(SetupError, "Rust"):
                create_tracker(name, class_change_policy="strict")

    def test_real_rust_class_policy_and_color_flicker(self):
        for policy in ("strict", "allow"):
            for labels in (("crate", "cart", "crate"), ("R", "B", "R")):
                with self.subTest(policy=policy, labels=labels):
                    tracker = create_tracker("rust", labels[:2], policy)
                    try:
                        ids = [tracker.update(Frame(i, i*50, None),
                               [Detection(code, .95, (0, 0, 10, 10))])[0].id for i, code in enumerate(labels)]
                        self.assertEqual(ids, [1, 2, 1] if policy == "strict" else [1, 1, 1])
                        tracker.reset()
                        self.assertEqual(tracker.update(Frame(0, 0, None),
                                         [Detection(labels[0], .95, (0, 0, 10, 10))])[0].id, 1)
                    finally:
                        tracker.close()
