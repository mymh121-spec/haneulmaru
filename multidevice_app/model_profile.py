"""Portable detection settings shared by dataset import and PC inference."""
import json
import hashlib
from pathlib import Path

from .contracts import SetupError

LAYOUTS = {"yolo_raw", "yolo_raw_rows", "yolo_objectness_rows", "xyxy_score_class"}
LEGACY_LAYOUT = "yolo11_raw_1_7_n"


def model_sha256(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SetupError(f"모델 파일을 읽을 수 없습니다: {path}") from exc
    return digest.hexdigest()


def profile_metadata(config):
    classes = validate_classes(config.get("classes", ["R", "B", "G"]))
    policy = config.get("class_change_policy", "allow")
    if policy not in ("allow", "strict"):
        raise SetupError("class_change_policy는 allow 또는 strict여야 합니다.")
    aliases = config.get("display_names", {})
    if not isinstance(aliases, dict) or any(
        key not in classes or not isinstance(value, str) or not value.strip() or not value.isprintable()
        for key, value in aliases.items()
    ):
        raise SetupError("display_names에는 학습 클래스별 표시 이름을 지정하세요.")
    deployment = config.get("deployment")
    if "deployment" in config:
        if not isinstance(deployment, dict):
            raise SetupError("deployment에는 model_sha256와 classes가 필요합니다.")
        digest = deployment.get("model_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise SetupError("deployment.model_sha256는 소문자 SHA-256이어야 합니다.")
        if validate_classes(deployment.get("classes")) != classes:
            raise SetupError("배포 기록의 학습 클래스 이름/순서가 프로필과 다릅니다.")
    result = dict(class_change_policy=policy, display_names=dict(aliases))
    if deployment is not None:
        result["deployment"] = deployment
    return result


def verify_model(config, path):
    metadata = profile_metadata(config)
    deployment = metadata.get("deployment")
    if deployment is None:
        return dict(status="unverified", reason="missing_deployment_record")
    actual = model_sha256(path)
    if actual != deployment["model_sha256"]:
        raise SetupError("모델 SHA-256이 배포 기록과 다릅니다. 올바른 가중치와 프로필을 함께 사용하세요.")
    return dict(status="verified", model_sha256=actual)


def validate_classes(names):
    if not isinstance(names, list) or not names or any(
        not isinstance(n, str) or not n or n.strip() != n or not n.isprintable() or n == "UNKNOWN" for n in names
    ):
        raise SetupError("classes에는 비어 있지 않은 클래스 이름을 ID 순서로 지정하세요. UNKNOWN은 예약어입니다.")
    if len(set(names)) != len(names):
        raise SetupError("클래스 이름이 중복되었습니다.")
    return names


def detector_options(engine):
    layout = engine.get("output_layout", LEGACY_LAYOUT)
    classes = engine.get("classes", ["R", "B", "G"])
    validate_classes(classes)
    if layout == LEGACY_LAYOUT:
        if classes != ["R", "B", "G"]:
            raise SetupError("기존 R/B/G 형식입니다. 새 클래스에는 yolo_raw 등의 output_layout을 지정하세요.")
        layout = "yolo_raw"
    if layout not in LAYOUTS:
        raise SetupError(f"지원하지 않는 출력 형식: {layout}")
    agnostic = engine.get("agnostic_nms", engine.get("output_layout", LEGACY_LAYOUT) == LEGACY_LAYOUT)
    if type(agnostic) is not bool:
        raise SetupError("agnostic_nms는 true 또는 false여야 합니다.")
    return dict(classes=classes, output_layout=layout, agnostic_nms=agnostic)


def load_profile(path):
    path = Path(path).resolve()
    try:
        profile = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"모델 프로필을 읽을 수 없습니다: {path}") from exc
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise SetupError("모델 프로필 schema_version=1이 필요합니다.")
    required = ("model_path", "input_size", "classes", "output_layout")
    if any(key not in profile for key in required):
        raise SetupError(f"모델 프로필 필수 항목: {', '.join(required)}")
    if not isinstance(profile["model_path"], str) or not profile["model_path"].strip():
        raise SetupError("모델 프로필의 model_path가 비어 있습니다.")
    size = profile["input_size"]
    if type(size) is not int or not 32 <= size <= 2048 or size % 32:
        raise SetupError("input_size는 32~2048 범위의 32 배수여야 합니다.")
    names = {k: profile.get(k, default) for k, default in (("input_name", "images"), ("output_name", "output0"))}
    if any(not isinstance(v, str) or not v.strip() or "\x00" in v for v in names.values()):
        raise SetupError("모델의 입력/출력 이름이 잘못되었습니다.")
    return dict(model_path=str(path.parent / profile["model_path"]), input_size=size, **names,
                **detector_options(profile), **profile_metadata(profile))


def seal_profile(profile_path, training_classes_path, output):
    """Bind weights to an explicit training-time class list; never infer it from aliases."""
    profile_path, output = Path(profile_path), Path(output)
    config = load_profile(profile_path)
    classes = validate_classes(json.loads(Path(training_classes_path).read_text(encoding="utf-8-sig")))
    if classes != config["classes"]:
        raise SetupError("학습 클래스 이름/순서가 프로필과 다릅니다.")
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    # Keep the model reference valid even when the output lives in another folder.
    import os
    profile["model_path"] = os.path.relpath(config["model_path"], output.resolve().parent)
    profile["deployment"] = dict(model_sha256=model_sha256(config["model_path"]), classes=classes)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="학습 시 저장한 클래스 목록과 모델 해시를 배포 프로필에 기록")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--training-classes", type=Path, required=True, help="클래스 ID 순서의 JSON 문자열 배열")
    parser.add_argument("--output", type=Path, required=True, help="새 배포 프로필 경로")
    args = parser.parse_args()
    try:
        seal_profile(args.profile, args.training_classes, args.output)
    except (SetupError, OSError, ValueError) as exc:
        parser.exit(1, f"배포 프로필 생성 실패: {exc}\n")
