"""Adapters for the PC Rust worker. stdout is machine data, stderr is operator feedback."""
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import threading
import time

from .contracts import Detection, SetupError
from .model_profile import detector_options
from .worker import PipeWorker, timeout_seconds

ROOT = Path(__file__).resolve().parent


def binary():
    name = "haneulmaru.exe" if os.name == "nt" else "haneulmaru"
    choices = [ROOT / "bin" / name, ROOT / "rust" / "target" / "release" / name]
    for path in choices:
        if path.is_file():
            return str(path)
    raise SetupError("Rust 실행 파일이 없습니다. multidevice_app/rust에서 cargo build --release --locked를 실행하세요.")


class RustEngine:
    name, ready, reason = "rust", True, None

    def __init__(self, config, root):
        model = root / config["model_path"]
        if not model.is_file():
            raise SetupError(f"학습된 ONNX 파일을 찾을 수 없습니다: {model}")
        if config.get("device", "cpu") != "cpu":
            raise SetupError("Rust 엔진은 CPU를 사용합니다.")
        options = detector_options(config)
        startup_timeout = timeout_seconds(config.get("startup_timeout_s", 30))
        self.request_timeout = timeout_seconds(config.get("request_timeout_s", 5))
        self.worker = PipeWorker([binary(), "detect-profile", str(model), str(config["input_size"]),
            str(config["confidence"]), str(config["nms_iou"]), config["input_name"], config["output_name"]])
        self.proc = self.worker.proc
        try:
            ready = self.worker.exchange(json.dumps(options, ensure_ascii=True).encode() + b"\n",
                                         startup_timeout, "Rust model startup")
            if ready.strip() != b"ready":
                raise SetupError("Rust에서 ONNX 모델을 열 수 없습니다. 위의 런타임 메시지를 확인하세요.")
        except Exception:
            self.close()
            raise

    def detect(self, frame):
        h, w = frame.image.shape[:2]
        try:
            result = self.worker.exchange(struct.pack("<II", w, h) + frame.image.tobytes(order="C"),
                                          self.request_timeout, "Rust frame inference")
            decoded = json.loads(result)
            if not isinstance(decoded, list):
                raise ValueError("Expected a detection list")
            return [Detection(d["code"], d["confidence"], tuple(d["xyxy"])).validate(w, h) for d in decoded]
        except SetupError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            self.close()
            raise SetupError("Rust inference returned an invalid detection response") from exc

    def close(self):
        self.worker.close()


class WifiImu:
    def __init__(self, bind="0.0.0.0:8793", model=None):
        self.bind, self.model = bind, model
        self.proc = self.thread = self.latest = None
        self.lock = threading.Lock()

    def start(self):
        env = dict(os.environ)
        env.setdefault("HANEULMARU_PAIR_KEY", secrets.token_hex(8))
        args = [binary(), "imu", self.bind]
        if self.model:
            args.append(str(self.model))
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, env=env)
        try:
            if json.loads(self.proc.stdout.readline() or b"{}").get("event") != "ready":
                raise SetupError("Rust Wi-Fi 수신기 시작 실패. UDP 주소와 포트 사용 여부를 확인하세요.")
        except Exception:
            self.stop()
            raise
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                event = json.loads(line)
                if event.get("event") == "sample":
                    with self.lock:
                        self.latest = time.monotonic(), event
        finally:
            with self.lock:
                self.latest = None

    def snapshot(self):
        with self.lock:
            item = self.latest
        age = (time.monotonic() - item[0]) * 1000 if item else None
        fresh = item is not None and age <= 350
        event = item[1] if item else {}
        classification = (dict(valid=True, direction=event["direction"], rate_dps=event["rate_dps"],
            similarity=event["similarity"], age_ms=age, source=event["source"], session=event["session"],
            board_t_ms=event["sample"]["t_ms"]) if fresh else
            dict(valid=False, direction=None, reason="imu_unavailable_or_stale"))
        return dict(session=event.get("session"), source=event.get("source") if fresh else None,
            sample=event.get("sample") if fresh else None, age_ms=age if fresh else None,
            classification=classification, sources={"wifi": {"fresh": fresh}})

    def stop(self):
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
            self.proc.wait()
            if self.thread:
                self.thread.join()
            self.proc.stdout.close()
