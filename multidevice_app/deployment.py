"""Portable model/profile/evidence bundles; no training or network required."""
import json
from pathlib import Path
import shutil
import tempfile

from .contracts import SetupError
from .model_profile import load_profile, model_sha256, validate_classes, verify_model


def bundle(profile_path, training_classes_path, output, dataset_report=None, evaluation=None):
    profile_path, output = Path(profile_path), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Bundle output must be a new directory")
    cfg = load_profile(profile_path)
    classes = validate_classes(json.loads(Path(training_classes_path).read_text(encoding="utf-8-sig")))
    if classes != cfg["classes"]:
        raise SetupError("Training class order does not match profile")
    model = Path(cfg["model_path"])
    verify_model(cfg, model)
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".bundle-") as tmp:
        stage = Path(tmp) / "release"
        stage.mkdir()
        model_name = "model" + model.suffix.lower()
        shutil.copyfile(model, stage / model_name)
        digest = model_sha256(stage / model_name)
        profile.update(model_path=model_name, deployment=dict(model_sha256=digest, classes=classes))
        (stage / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "training-classes.json").write_text(json.dumps(classes, ensure_ascii=False) + "\n", encoding="utf-8")
        evidence = {}
        for name, source in (("dataset-report.json", dataset_report), ("evaluation.json", evaluation)):
            if source is None:
                continue
            data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("Bundle evidence must be a JSON object")
            if name == "dataset-report.json" and data.get("classes") != classes:
                raise ValueError("Dataset report class order does not match model")
            if name == "evaluation.json":
                if data.get("metric") != "class_aware_iou_assignment":
                    raise ValueError("Bundle requires a single-model evaluation report")
                if set(data.get("per_class", {})) - set(classes) - {"UNKNOWN"}:
                    raise ValueError("Evaluation classes do not match model profile")
                hashes = data.get("model_hashes", [])
                if hashes and hashes != [digest]:
                    raise ValueError("Evaluation refers to different model weights")
                evidence["evaluation_model_match"] = "verified" if hashes else "unverified"
            shutil.copyfile(source, stage / name)
        manifest = dict(schema_version=1, model_sha256=digest, evidence=evidence,
                        files={p.name: model_sha256(p) for p in sorted(stage.iterdir())})
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_bundle(stage)
        stage.rename(output)
    return manifest


def verify_bundle(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(files, dict) or not {"profile.json", "training-classes.json"} <= files.keys():
        raise ValueError("Invalid bundle manifest")
    for name, digest in files.items():
        path = root / name
        if Path(name).name != name or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Invalid bundle path")
        if model_sha256(path) != digest:
            raise SetupError(f"Bundle checksum mismatch: {name}")
    cfg = load_profile(root / "profile.json")
    model = Path(cfg["model_path"]).resolve()
    if model.parent != root or model.name not in files:
        raise ValueError("Model must be a checksummed file inside bundle")
    classes = json.loads((root / "training-classes.json").read_text(encoding="utf-8"))
    if classes != cfg["classes"]:
        raise SetupError("Bundle class order mismatch")
    status = verify_model(cfg, model)
    if status.get("model_sha256") != manifest.get("model_sha256"):
        raise SetupError("Bundle model identity mismatch")
    return status
