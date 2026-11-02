"""Portable detection settings shared by dataset import and PC inference."""
import json
from pathlib import Path

from .contracts import SetupError

LAYOUTS = {"yolo_raw", "yolo_raw_rows", "yolo_objectness_rows", "xyxy_score_class"}
LEGACY_LAYOUT = "yolo11_raw_1_7_n"


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
    return dict(model_path=str(path.parent / profile["model_path"]), input_size=size, **names, **detector_options(profile))
