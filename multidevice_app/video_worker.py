"""Isolated decoder: a blocked VideoCapture can be killed without hanging the host."""
import base64
import json
import os
from pathlib import Path
import sys
import numpy as np

from .contracts import Frame, SetupError
from .inputs import OpenCVSource
from .worker import PipeWorker, timeout_seconds


class IsolatedSource:
    def __init__(self, source, rotation=0, recording=False, timeout_s=5):
        self.timeout = timeout_seconds(timeout_s)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        self.worker = PipeWorker([sys.executable, "-u", "-m", "multidevice_app.video_worker"],
                                 env=env, max_response_bytes=64*1024*1024)
        try:
            response = self.worker.exchange(json.dumps(dict(source=str(source), rotation=rotation,
                recording=recording)).encode() + b"\n", self.timeout, "Video open")
            if json.loads(response) != {"ready": True}:
                raise SetupError("Video decoder could not open input")
        except Exception:
            self.close()
            raise

    def read(self):
        try:
            row = json.loads(self.worker.exchange(b"next\n", self.timeout, "Video read"))
            if row.get("eof") is True:
                return None
            height, width = row["shape"]
            if type(height) is not int or type(width) is not int or min(height, width) <= 0 or height*width > 16_000_000:
                raise ValueError("Invalid decoded frame dimensions")
            data = base64.b64decode(row["data"], validate=True)
            image = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            return Frame(row["sequence"], row["timestamp_ms"], image, row["timestamp_basis"])
        except (ValueError, KeyError, TypeError) as exc:
            self.close()
            raise SetupError("Invalid isolated video response") from exc

    def close(self):
        self.worker.close()


def main():
    cfg = json.loads(sys.stdin.buffer.readline())
    source = OpenCVSource(cfg["source"], cfg["rotation"], cfg["recording"])
    try:
        print(json.dumps({"ready": True}), flush=True)
        for command in sys.stdin.buffer:
            if command.strip() != b"next":
                raise ValueError("Unknown decoder request")
            frame = source.read()
            if frame is None:
                row = dict(eof=True)
            else:
                if frame.image.shape[0]*frame.image.shape[1] > 16_000_000:
                    raise ValueError("Frame exceeds 16 megapixels")
                row = dict(sequence=frame.sequence, timestamp_ms=frame.timestamp_ms,
                           timestamp_basis=frame.timestamp_basis, shape=frame.image.shape[:2],
                           data=base64.b64encode(frame.image.tobytes()).decode("ascii"))
            print(json.dumps(row), flush=True)
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Decoder exceptions may include source URL credentials.
        print("Video decoder failed", file=sys.stderr)
        raise SystemExit(1)
