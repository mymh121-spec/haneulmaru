"""Changing engine does not change input, tracking, or output contracts."""
from __future__ import annotations

import os
from pathlib import Path
from .contracts import Detection, SetupError


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
            raise SetupError("설정한 경로에 학습된 R/B/G best.pt가 필요합니다")
        self.model = YOLO(str(weights))
        if self.model.task != "detect" or self.model.names != {0: "R", 1: "B", 2: "G"}:
            raise ValueError("R/B/G 클래스 순서로 학습한 탐지 모델이 필요합니다")
        self.config = config

    def detect(self, frame):
        boxes = self.model.predict(
            frame.image, imgsz=self.config["input_size"], device=self.config["device"],
            conf=self.config["confidence"], iou=self.config["nms_iou"],
            agnostic_nms=True, rect=False, verbose=False, save=False)[0].boxes
        if boxes is None:
            return []
        return [Detection(self.model.names[int(cls)], float(score), tuple(map(float, box)))
                for box, score, cls in zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(),
                                           boxes.cls.cpu().tolist())]

    def close(self):
        self.model = None


class ClikaRTEngine:
    """Official Python ONNX entry point, with a deliberately narrow YOLO contract.

    Requires FP32, batch=1, static NCHW, nms=False YOLO11 3-class export.
    The adapter has not been executed against an SDK on this PC.
    Other exports must supply another decoder rather than guessing their layout.
    """
    name = "clikart"
    ready = True
    reason = None

    def __init__(self, config, root):
        if not os.environ.get("CLIKA_RT_LICENSE"):
            raise SetupError("CLIKA_RT_LICENSE에 발급받은 라이선스 또는 파일 경로를 설정하세요")
        try:
            import clika_runtime as crt
        except ImportError as exc:
            raise SetupError("공식 ClikaRT Python SDK를 먼저 설치하세요") from exc
        import numpy as np
        import cv2
        path = root / config["model_path"]
        if not path.is_file():
            raise SetupError("설정한 경로에 학습한 ONNX 모델이 필요합니다")
        if config.get("output_layout") != "yolo11_raw_1_7_n" or config["classes"] != ["R", "B", "G"]:
            raise ValueError("지원 형식: YOLO11 [1,7,N], 클래스 R/B/G, NMS 미포함")
        self.crt, self.np, self.cv2, self.config = crt, np, cv2, config
        # Load once, outside the frame loop. The downloaded SDK README takes
        # precedence if the issued release has a different Python API.
        self.model = crt.onnx.load(str(path), device=config["device"])
        size = config["input_size"]
        self.canvas = np.empty((size, size, 3), dtype=np.uint8)
        self.input_buffer = np.empty((1, 3, size, size), dtype=np.float32)

    def detect(self, frame):
        np, cv2, cfg = self.np, self.cv2, self.config
        h, w = frame.image.shape[:2]
        size = cfg["input_size"]
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        left, top = (size - nw) // 2, (size - nh) // 2
        image = self.canvas
        image.fill(114)
        image[top:top + nh, left:left + nw] = cv2.resize(frame.image, (nw, nh))
        tensor = self.input_buffer
        np.copyto(tensor[0], image[:, :, ::-1].transpose(2, 0, 1))
        tensor /= 255.0
        result = self.model(**{cfg["input_name"]: self.crt.tensor(tensor, device=cfg["device"])})
        if set(result) != {cfg["output_name"]}:
            raise ValueError("ONNX 출력 이름/개수가 설정과 다릅니다")
        raw = np.asarray(result[cfg["output_name"]].to("cpu").numpy())
        if raw.ndim != 3 or raw.shape[0:2] != (1, 7) or not np.isfinite(raw).all():
            raise ValueError("YOLO11 출력은 유한한 [1,7,N]이어야 합니다")
        rows = raw[0].T
        if (rows[:, 4:] < 0).any() or (rows[:, 4:] > 1).any():
            raise ValueError("클래스 출력이 0~1 확률이 아닙니다. ONNX export를 확인하세요")
        labels = rows[:, 4:].argmax(axis=1)
        scores = rows[:, 4:].max(axis=1)
        selected = (scores >= cfg["confidence"]) & (rows[:, 2] > 0) & (rows[:, 3] > 0)
        rows, scores, labels = rows[selected], scores[selected], labels[selected]
        if not len(rows):
            return []
        # Restore all boxes with array operations instead of Python loops.
        centers, half_sizes = rows[:, :2].astype(np.float64), rows[:, 2:4].astype(np.float64) / 2
        offset = np.array([left, top], dtype=np.float64)
        limits = np.array([w, h], dtype=np.float64)
        lo = np.clip((centers - half_sizes - offset) / scale, 0, limits)
        hi = np.clip((centers + half_sizes - offset) / scale, 0, limits)
        extents = hi - lo
        valid = (extents > 0).all(axis=1)
        boxes = np.concatenate((lo[valid], extents[valid]), axis=1).tolist()
        scores, labels = scores[valid], labels[valid]
        if not boxes:
            return []
        indices = np.asarray(cv2.dnn.NMSBoxes(boxes, scores.tolist(), cfg["confidence"],
                                            cfg["nms_iou"])).reshape(-1)
        output = []
        for index in indices:
            x, y, bw, bh = boxes[int(index)]
            if bw > 0 and bh > 0:
                output.append(Detection(cfg["classes"][int(labels[index])], float(scores[index]),
                                        (x, y, x + bw, y + bh)))
        return output

    def close(self):
        self.model = None
        self.canvas = None
        self.input_buffer = None


def create_engine(config, root: Path):
    choice = config["name"]
    constructors = {"disabled": lambda: DisabledEngine(),
                    "ultralytics": lambda: UltralyticsEngine(config, root),
                    "clikart": lambda: ClikaRTEngine(config, root)}
    if choice not in constructors:
        raise ValueError(f"지원하지 않는 엔진: {choice}")
    # Never silently replace failed ClikaRT inference with another engine.
    return constructors[choice]()
