"""Changing engine does not change input, tracking, or output contracts."""
from __future__ import annotations

from pathlib import Path
from .contracts import Detection, SetupError
from .model_profile import validate_classes


from .rust_host import RustEngine


class DisabledEngine:
    name = "disabled"
    ready = False
    reason = "인식 엔진 미연결: 실제 카메라·IMU 입력만 표시합니다."

    def detect(self, frame):
        return []

    def close(self):
        pass


class UltralyticsEngine:
    name = "ultralytics"
    ready = True
    reason = None

    def __init__(self, config, root):
        from ultralytics import YOLO
        weights = root / config["model_path"]
        if not weights.is_file():
            raise SetupError(f"학습된 .pt 파일을 찾을 수 없습니다: {weights}")
        self.model = YOLO(str(weights))
        names = validate_classes(config["classes"])
        if self.model.task != "detect" or self.model.names != dict(enumerate(names)):
            raise SetupError(".pt 탐지 모델의 클래스 이름/순서가 설정과 다릅니다.")
        self.config = config

    def detect(self, frame):
        boxes = self.model.predict(
            frame.image, imgsz=self.config["input_size"], device=self.config["device"],
            conf=self.config["confidence"], iou=self.config["nms_iou"],
            agnostic_nms=self.config.get("agnostic_nms", True), rect=False, verbose=False, save=False)[0].boxes
        if boxes is None:
            return []
        return [Detection(self.model.names[int(cls)], float(score), tuple(map(float, box)))
                for box, score, cls in zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(),
                                           boxes.cls.cpu().tolist())]

    def close(self):
        self.model = None


def create_engine(config, root: Path):
    choice = config["name"]
    constructors = {"disabled": lambda: DisabledEngine(),
                    "ultralytics": lambda: UltralyticsEngine(config, root),
                    "rust": lambda: RustEngine(config, root)}
    if choice not in constructors:
        raise ValueError(f"지원하지 않는 엔진: {choice}")
    return constructors[choice]()
