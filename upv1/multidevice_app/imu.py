"""Trained direction classification, independent from the inference engine."""
import math


class DirectionClassifier:
    def __init__(self, model):
        if (model.get("format") != "nu40-imu-direction-model" or model.get("schema_version") != 1
                or model.get("units") != "degrees_per_second"):
            raise ValueError("기존 NU40 방향 학습 JSON이 필요합니다")
        self.threshold = model["rate_threshold_dps"]
        self.similarity = model["similarity_threshold"]
        if not math.isfinite(self.threshold) or self.threshold <= 0 or not 0 <= self.similarity <= 1:
            raise ValueError("Invalid training thresholds")
        self.vectors = {}
        for key in ("up", "down", "left", "right"):
            v = model["directions"][key]
            if len(v) != 3 or not all(math.isfinite(x) for x in v):
                raise ValueError("Invalid trained direction")
            norm = math.hypot(*v)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError("Invalid trained direction")
            self.vectors[key] = [x / norm for x in v]

    def classify(self, state):
        if not isinstance(state, dict):
            return dict(valid=False, direction=None, reason="invalid_imu_state")
        sample, age = state.get("sample"), state.get("age_ms")
        if not isinstance(sample, dict) or type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= 350:
            return dict(valid=False, direction=None, reason="imu_unavailable_or_stale")
        vector = [sample.get(k) for k in ("gx", "gy", "gz")]
        stamp = sample.get("t_ms")
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in vector) or
                type(stamp) is not int or not 0 <= stamp <= 0xffffffff):
            return dict(valid=False, direction=None, reason="invalid_gyro")
        rate = math.hypot(*vector)
        if not math.isfinite(rate):
            return dict(valid=False, direction=None, reason="invalid_gyro")
        direction, similarity = None, None
        if rate >= self.threshold:
            scores = sorted(((sum(a * b / rate for a, b in zip(v, vector)), name)
                             for name, v in self.vectors.items()), reverse=True)
            if scores[0][0] >= self.similarity and scores[0][0] - scores[1][0] >= 0.08:
                similarity, direction = scores[0]
        return dict(valid=True, direction=direction, similarity=similarity,
                    rate_dps=rate, source=state.get("source"), age_ms=age,
                    board_t_ms=sample["t_ms"], session=state.get("session"))
