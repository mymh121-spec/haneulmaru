from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Protocol
import math


class SetupError(RuntimeError):
    """Safe, application-authored setup message; no library credentials."""


@dataclass(frozen=True)
class Frame:
    """Upright, unmirrored BGR uint8 image; timestamp in host monotonic ms.

    For network video this is decode time, not S22 sensor exposure time.
    A native camera adapter must map sensor time into the same clock first.
    """
    sequence: int
    timestamp_ms: float
    image: Any
    timestamp_basis: str = "host_decode_time"


@dataclass(frozen=True)
class Detection:
    code: str
    confidence: float
    xyxy: tuple[float, float, float, float]
    id: int | None = None

    def validate(self, width, height):
        if self.code not in ("R", "B", "G", "UNKNOWN"):
            raise ValueError("Unknown class")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid confidence")
        if len(self.xyxy) != 4 or not all(math.isfinite(v) for v in self.xyxy):
            raise ValueError("Invalid box")
        x1, y1, x2, y2 = self.xyxy
        if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
            raise ValueError("Box outside upright source image")
        return self

    def packet(self):
        return asdict(self)


class Engine(Protocol):
    """An engine never owns the camera, IMU, tracker, or UI."""
    name: str
    ready: bool
    reason: str | None

    def detect(self, frame: Frame) -> list[Detection]: ...
    def close(self) -> None: ...


class Tracker(Protocol):
    def update(self, frame: Frame, detections: list[Detection]) -> list[Detection]: ...
    def reset(self) -> None: ...


class FrameSource(Protocol):
    def read(self) -> Frame | None: ...
    def close(self) -> None: ...


class ImuSource(Protocol):
    def snapshot(self) -> dict: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
