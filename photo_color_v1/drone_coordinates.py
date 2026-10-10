"""Session-local East/North/Up observation rays; independent of AR rendering."""
from __future__ import annotations

import math
import time
import uuid


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _unit(vector):
    size = math.hypot(*vector)
    if not size or not all(_number(v) for v in vector):
        raise ValueError("유효한 방향 벡터가 필요합니다")
    return [v / size for v in vector]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _bearing(east, north):
    angle = math.degrees(math.atan2(east, north)) % 360
    return 0.0 if angle >= 360 - 1e-9 else angle


def projection_parameters(config, width, height):
    """Scale supplied intrinsics, or use an explicitly labelled provisional FOV."""
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise ValueError("영상의 실제 픽셀 크기가 필요합니다")
    intrinsics = config.get("intrinsics")
    if intrinsics is not None:
        required = ("fx", "fy", "cx", "cy", "width", "height")
        if any(not _number(intrinsics.get(k)) for k in required):
            raise ValueError("intrinsics: fx/fy/cx/cy/width/height가 필요합니다")
        if min(intrinsics[k] for k in ("fx", "fy", "width", "height")) <= 0:
            raise ValueError("초점 및 해상도는 양수여야 합니다")
        sx, sy = width / intrinsics["width"], height / intrinsics["height"]
        return dict(fx=intrinsics["fx"] * sx, fy=intrinsics["fy"] * sy,
                    cx=intrinsics["cx"] * sx, cy=intrinsics["cy"] * sy,
                    source="supplied_intrinsics", calibrated=bool(config.get("calibrated", False)))
    fov = config.get("vertical_fov_deg")
    if not _number(fov) or not 0 < fov < 170:
        raise ValueError("명시적인 임시 vertical_fov_deg 또는 intrinsics가 필요합니다")
    focal = height / (2 * math.tan(math.radians(fov) / 2))
    return dict(fx=focal, fy=focal, cx=width / 2, cy=height / 2,
                source="assumed_vertical_fov", vertical_fov_deg=fov, calibrated=False)


class NorthCoordinateSession:
    """The first camera heading becomes north; distance remains unknown.

    heading_deg is clockwise, pitch_deg is positive upwards, roll_deg is
    clockwise. This session assumes a stationary camera origin at (0,0,0).
    The 1-unit ray is dimensionless and never a 1-metre object position.
    """
    def __init__(self, projection=None):
        self.projection = projection or {"vertical_fov_deg": 55, "intrinsics": None}
        self.session_id = None
        self.initial_heading = None

    def start(self, pose):
        self._validate_pose(pose)
        self.session_id = uuid.uuid4().hex
        self.initial_heading = pose["heading_deg"]
        self.started_at_ms = round(time.time() * 1000)

    @staticmethod
    def _validate_pose(pose):
        if not isinstance(pose, dict) or any(not _number(pose.get(k)) for k in ("heading_deg", "pitch_deg", "roll_deg")):
            raise ValueError("heading_deg/pitch_deg/roll_deg가 필요합니다")

    def record(self, tracks, width, height, pose, captured_at_ms=None):
        self._validate_pose(pose)
        projection = projection_parameters(self.projection, width, height)
        valid_pose = pose.get("valid", True) is True
        if self.session_id is None and valid_pose:
            self.start(pose)
        heading = (pose["heading_deg"] - (self.initial_heading if self.initial_heading is not None else pose["heading_deg"])) % 360
        h, p, r = map(math.radians, (heading, pose["pitch_deg"], pose["roll_deg"]))
        forward = [math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)]
        right = [math.cos(h), -math.sin(h), 0]
        up = [-math.sin(h) * math.sin(p), -math.cos(h) * math.sin(p), math.cos(p)]
        rolled_right = [right[i] * math.cos(r) - up[i] * math.sin(r) for i in range(3)]
        rolled_up = [right[i] * math.sin(r) + up[i] * math.cos(r) for i in range(3)]
        observations = []
        for track in tracks:
            box = track.get("xyxy")
            if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_number(v) for v in box):
                raise ValueError("유효한 xyxy 드론 박스가 필요합니다")
            x1, y1, x2, y2 = box
            if not (0 <= x1 <= x2 <= width and 0 <= y1 <= y2 <= height):
                raise ValueError("xyxy 박스는 실제 영상 범위 안에 있어야 합니다")
            u, v = (x1 + x2) / 2, (y1 + y2) / 2
            x, y = (u - projection["cx"]) / projection["fx"], (projection["cy"] - v) / projection["fy"]
            direction = _unit([forward[i] + x * rolled_right[i] + y * rolled_up[i] for i in range(3)]) if valid_pose else None
            horizontal = math.hypot(*direction[:2]) if direction else 0
            observations.append({
                "id": track.get("id"), "code": track.get("code", "UNKNOWN"),
                "image_center_px": [u, v], "image_uv": [u / width, v / height],
                "ray_origin_enu_m": [0, 0, 0], "direction_enu_unit": direction,
                "bearing_deg": _bearing(direction[0], direction[1]) if direction and horizontal > 1e-9 else None,
                "elevation_deg": math.degrees(math.atan2(direction[2], horizontal)) if direction else None,
                "range_m": None, "position_enu_m": None,
                "status": "direction_estimate" if valid_pose else "pose_unavailable",
            })
        return {
            "schema_version": 1, "session_id": self.session_id,
            "captured_at_ms": captured_at_ms if captured_at_ms is not None else round(time.time() * 1000),
            "reference": {"axes": ["east", "north", "up"], "north": "initial_camera_heading_assumed",
                          "origin_enu_m": [0, 0, 0], "origin_mode": "stationary_camera_assumed",
                          "initial_heading_deg": self.initial_heading},
            "camera_pose": {**pose, "heading_deg": heading},
            "image_size_px": [width, height], "projection": projection,
            "observations": observations,
        }


class GyroPose:
    """Continuous signed projections of trained axes, with no gesture threshold.

    Initial pitch is assumed level, roll is ignored. Missing samples make pose
    uncertain until reset; USB/BLE transport changes never reset north.
    """
    def __init__(self, model):
        if model.get("format") != "nu40-imu-direction-model" or model.get("units") != "degrees_per_second":
            raise ValueError("NU40 각속도 방향 학습 파일이 필요합니다")
        directions = model["directions"]
        vectors = {}
        for name in ("up", "down", "left", "right"):
            value = directions[name]
            if len(value) != 3:
                raise ValueError("3축 학습 벡터가 필요합니다")
            vectors[name] = _unit(value)
        self.horizontal = _unit([a - b for a, b in zip(vectors["right"], vectors["left"])])
        self.vertical = _unit([a - b for a, b in zip(vectors["up"], vectors["down"])])
        self.coupling = _dot(self.horizontal, self.vertical)
        if 1 - self.coupling ** 2 < 0.1:
            raise ValueError("학습한 가로/세로 회전축을 구분할 수 없습니다")
        self.reset()

    def reset(self):
        self.heading = self.pitch = 0.0
        self.previous = None
        self.session = None
        self.source = None
        self.valid = False
        self.uncertain = False

    def consume(self, state):
        sample = state.get("sample")
        fresh = _number(state.get("age_ms")) and 0 <= state["age_ms"] <= 350
        if not isinstance(sample, dict) or not fresh or state.get("source") not in ("USB", "BLE"):
            if self.previous is not None:
                self.uncertain = True
            self.previous = None
            self.valid = False
            return self.snapshot()
        stamp = sample.get("t_ms")
        vector = [sample.get(k) for k in ("gx", "gy", "gz")]
        if type(stamp) is not int or not 0 <= stamp <= 0xffffffff or not all(_number(v) for v in vector):
            self.valid = False
            self.uncertain = True
            return self.snapshot()
        if self.session is not None and state.get("session") != self.session:
            self.uncertain = True
            self.previous = None
        self.session = state.get("session")
        dt_ms = ((stamp - self.previous) % 0x100000000) if self.previous is not None else 0
        # A duplicate on BLE after USB may be slightly older; ignore it rather
        # than integrating backwards or changing the timestamp baseline.
        if dt_ms > 0x80000000:
            if 0x100000000 - dt_ms > 200 or state["source"] == self.source:
                self.previous = stamp
                self.uncertain = True
                self.valid = False
            return self.snapshot()
        self.previous = stamp
        self.source = state["source"]
        if dt_ms > 200:
            self.uncertain = True
        elif dt_ms:
            h, v = _dot(vector, self.horizontal), _dot(vector, self.vertical)
            denominator = 1 - self.coupling ** 2
            yaw_rate = (h - self.coupling * v) / denominator
            pitch_rate = (v - self.coupling * h) / denominator
            # Keep slow turns: strong-gesture gating and display sensitivity
            # are deliberately absent from the coordinate pose path.
            self.heading = (self.heading + yaw_rate * dt_ms / 1000) % 360
            self.pitch = max(-89.9, min(89.9, self.pitch + pitch_rate * dt_ms / 1000))
        self.valid = not self.uncertain
        return self.snapshot()

    def snapshot(self):
        return {"heading_deg": self.heading, "pitch_deg": self.pitch, "roll_deg": 0,
                "valid": self.valid, "source": "NU40_trained_axis_gyro_integration",
                "quality": "relative_unleveled_gyro_estimate" if self.valid else "imu_gap_restart_required"}
