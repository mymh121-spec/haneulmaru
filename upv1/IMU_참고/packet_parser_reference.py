"""Extracted local parser reference. No serial connection, no firmware upload."""
from typing import Any
import math,json,time

def _number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return round(number, 4)


def normalize_packet(value: Any) -> dict[str, Any]:
    """Validate and normalize one sensor packet."""
    if not isinstance(value, dict):
        raise ValueError("packet must be an object")

    # andrew.ino uses hand/device_id plus g and deg/s values, while the
    # original dashboard protocol uses board plus m/s^2 and rad/s values.
    # Normalize both formats here so every page receives one stable schema.
    board = str(value.get("board", value.get("board_id", ""))).strip().upper()
    hand = str(value.get("hand", "")).strip().upper()
    device_id = str(value.get("device_id", "")).strip().lower()
    if not board:
        if hand in {"L", "LEFT"} or device_id.endswith("-left"):
            board = "LEFT"
        elif hand in {"R", "RIGHT"} or device_id.endswith("-right"):
            board = "RIGHT"
    if not board or len(board) > 24:
        raise ValueError("invalid board")

    imu = value.get("imu")
    if value.get("type") == "imu" and isinstance(imu, dict):
        accel = {
            "x": _number(float(imu["ax_g"]) * 9.80665),
            "y": _number(float(imu["ay_g"]) * 9.80665),
            "z": _number(float(imu["az_g"]) * 9.80665),
        }
        gyro = {
            "x": _number(math.radians(float(imu["gx_dps"]))),
            "y": _number(math.radians(float(imu["gy_dps"]))),
            "z": _number(math.radians(float(imu["gz_dps"]))),
        }
        calibration = str(value.get("calibration", "")).strip().lower()
        return {
            "type": "sensor",
            "board": board,
            "seq": int(value.get("seq", 0)),
            "timestamp_ms": int(value.get("ts_ms", value.get("timestamp_ms", 0))),
            "received_at": round(time.time() * 1000),
            "accel": accel,
            "gyro": gyro,
            "board_calibration_capable": True,
            "calibrated_on_board": calibration == "ready",
            "board_stationary": bool(value.get("stationary", False)),
            "mode_id": str(value.get("mode_id", "raw")),
            "zero_valid": bool(value.get("zero_valid", False)),
            "firmware_calibration": calibration or "unknown",
            "device_id": device_id,
        }

    accel = value.get("accel", {})
    gyro = value.get("gyro", {})
    if not isinstance(accel, dict) or not isinstance(gyro, dict):
        raise ValueError("accel and gyro must be objects")

    packet = {
        "type": "sensor",
        "board": board,
        "seq": int(value.get("seq", 0)),
        "timestamp_ms": int(value.get("timestamp_ms", 0)),
        "received_at": round(time.time() * 1000),
        "accel": {axis: _number(accel[axis]) for axis in "xyz"},
        "gyro": {axis: _number(gyro[axis]) for axis in "xyz"},
        "board_calibration_capable": "calibrated_on_board" in value,
        "calibrated_on_board": bool(value.get("calibrated_on_board", False)),
        "board_stationary": bool(value.get("stationary", False)),
    }
    raw_gyro = value.get("gyro_raw")
    if isinstance(raw_gyro, dict) and all(axis in raw_gyro for axis in "xyz"):
        packet["gyro_raw"] = {axis: _number(raw_gyro[axis]) for axis in "xyz"}
    return packet


def parse_sensor_line(line: str) -> dict[str, Any]:
    """Parse NDJSON or the legacy BOARD,ax,ay,az,gx,gy,gz CSV format."""
    text = line.strip()
    if not text:
        raise ValueError("empty line")
    if text.startswith("{"):
        return normalize_packet(json.loads(text))

    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 7:
        raise ValueError("not a sensor packet")
    return normalize_packet(
        {
            "board": parts[0],
            "accel": dict(zip("xyz", parts[1:4])),
            "gyro": dict(zip("xyz", parts[4:7])),
        }
    )


