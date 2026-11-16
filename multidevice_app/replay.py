"""Deterministic video-clock IMU replay and cached detections, without devices."""
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path

from .contracts import Detection, SetupError


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("expected an object")
                yield value
            except ValueError as exc:
                raise ValueError(f"{Path(path).name}:{number}: invalid JSON object") from exc


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ImuReplay:
    """Index arrival events once; seeking never attaches a future measurement.

    time_ms is measurement time on the video clock; available_ms optionally
    models delivery delay. Rows may arrive out of order. Latest measurement
    wins, so a late old event cannot replace a more recent sample.
    """
    def __init__(self, path, offset_ms=0, max_age_ms=350):
        if not finite(offset_ms) or not finite(max_age_ms) or max_age_ms <= 0:
            raise ValueError("Invalid IMU replay timing")
        self.offset_ms, self.max_age_ms = offset_ms, max_age_ms
        events = []
        identities = set()
        for row in read_jsonl(path):
            stamp = row.get("time_ms")
            arrival = row.get("available_ms", stamp)
            sample = row.get("sample")
            session = row.get("session")
            if (not finite(stamp) or stamp < 0 or not finite(arrival) or arrival < stamp
                    or not isinstance(session, str) or not session
                    or not isinstance(sample, dict) or type(sample.get("t_ms")) is not int
                    or not 0 <= sample["t_ms"] <= 0xffffffff
                    or any(not finite(sample.get(k)) for k in ("gx", "gy", "gz"))):
                raise ValueError("IMU rows require time_ms, session and a valid gyro sample")
            identity = (stamp, session)
            if identity in identities:
                raise ValueError("Duplicate IMU measurement time/session")
            identities.add(identity)
            events.append((arrival, stamp, row))
        self.times, self.latest = [], []
        best = None
        for arrival, stamp, row in sorted(events, key=lambda event: (event[0], event[1])):
            if best is None or stamp >= best["time_ms"]:
                best = row
            self.times.append(arrival)
            self.latest.append(best)

    def snapshot_at(self, video_ms):
        if not finite(video_ms) or video_ms < 0:
            raise ValueError("Invalid video timestamp")
        clock = video_ms - self.offset_ms
        index = bisect_right(self.times, clock) - 1
        row = self.latest[index] if index >= 0 else None
        age = clock - row["time_ms"] if row else None
        fresh = row is not None and 0 <= age <= self.max_age_ms
        return dict(sample=dict(row["sample"]) if fresh else None,
                    source="recorded_imu" if fresh else None, age_ms=age,
                    session=row["session"] if row else None,
                    measurement_ms=row["time_ms"] + self.offset_ms if row else None,
                    alignment="recorded_video_clock", sources={"replay": {"fresh": fresh}})


class DetectionReplay:
    """Re-run tracking from cached raw detections instead of loading a model."""
    name, ready, reason = "recorded_detections", True, None
    display_names = {}
    model_verification = {"status": "not_applicable", "reason": "cached_detections"}

    def __init__(self, path):
        self.frames = {}
        metadata = None
        for row in read_jsonl(path):
            key = row.get("frame_sequence")
            if type(key) is not int or key < 0 or key in self.frames:
                raise ValueError("Cached detections require unique frame_sequence values")
            size = row.get("image_size_px")
            stamp = row.get("frame_timestamp_ms")
            hits = row.get("detections")
            if (not isinstance(size, list) or len(size) != 2 or
                    any(type(v) is not int or v <= 0 for v in size) or
                    not finite(stamp) or stamp < 0 or not isinstance(hits, list)):
                raise ValueError("Cached detections require image_size_px, timestamp and detections")
            decoded = [Detection(d["code"], d["confidence"], tuple(d["xyxy"])).validate(*size) for d in hits]
            digest = row.get("frame_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Cached detections require decoded frame_sha256")
            current = (row.get("source_model_verification", self.model_verification), row.get("display_names", {}))
            if metadata is not None and metadata != current:
                raise ValueError("Cached detections mix model metadata")
            metadata = current
            self.frames[key] = (stamp, size, decoded, digest)
        if metadata is not None:
            self.model_verification, self.display_names = metadata

    def detect(self, frame):
        value = self.frames.get(frame.sequence)
        if value is None:
            raise SetupError(f"No cached detections for frame {frame.sequence}")
        stamp, size, detections, digest = value
        if size != list(frame.image.shape[1::-1]) or abs(stamp - frame.timestamp_ms) > .01:
            raise SetupError("Cached detections do not match video dimensions/time")
        if hashlib.sha256(frame.image.tobytes()).hexdigest() != digest:
            raise SetupError("Cached detections do not match video pixels")
        return list(detections)

    def close(self):
        pass


class CachingEngine:
    def __init__(self, engine, stream):
        self.engine, self.stream = engine, stream

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def detect(self, frame):
        hits = self.engine.detect(frame)
        write_cached_detections(self.stream, frame, [d.packet() for d in hits],
                               getattr(self.engine, "model_verification", {"status": "unverified"}),
                               getattr(self.engine, "display_names", {}))
        return hits

    def close(self):
        self.engine.close()


def write_cached_detections(stream, frame, detections, verification, aliases):
    stream.write(json.dumps(dict(frame_sequence=frame.sequence, frame_timestamp_ms=frame.timestamp_ms,
        image_size_px=list(frame.image.shape[1::-1]), frame_sha256=hashlib.sha256(frame.image.tobytes()).hexdigest(),
        source_model_verification=verification, display_names=aliases, detections=detections),
        ensure_ascii=False, allow_nan=False) + "\n")
