"""Isolated decoder: a blocked VideoCapture can be killed without hanging the host."""
import json
import math
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
                                 env=env, max_response_bytes=65536)
        try:
            response = self.worker.exchange(json.dumps(dict(source=str(source), rotation=rotation,
                recording=recording)).encode() + b"\n", self.timeout, "Video open")
            if json.loads(response) != {"ready": True, "video_protocol": 2}:
                raise SetupError("Video decoder could not open input")
        except Exception:
            self.close()
            raise

    def read(self):
        # Header and raw pixels share the same I/O thread, buffer and deadline.
        return self.worker.exchange(b"next\n", self.timeout, "Video read", response_reader=self._read_frame)

    def _read_frame(self):
        row = json.loads(self.worker.readline())
        if row.get("eof") is True:
            return None
        height, width = row["shape"]
        if type(height) is not int or type(width) is not int or min(height, width) <= 0 or height*width > 16_000_000:
            raise ValueError("Invalid decoded frame dimensions")
        stamp, sequence, basis = row["timestamp_ms"], row["sequence"], row["timestamp_basis"]
        if (type(sequence) is not int or sequence < 0 or type(stamp) not in (int,float)
                or not math.isfinite(stamp) or stamp < 0 or not isinstance(basis,str)):
            raise ValueError("Invalid decoded frame identity/time")
        size = height*width*3
        data = self.worker.reader.read(size)
        if len(data) != size:
            raise ValueError("Truncated decoded frame pixels")
        image = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
        return Frame(sequence, stamp, image, basis)

    def close(self):
        self.worker.close()


def main():
    cfg = json.loads(sys.stdin.buffer.readline())
    source = OpenCVSource(cfg["source"], cfg["rotation"], cfg["recording"])
    try:
        print(json.dumps({"ready": True, "video_protocol": 2}), flush=True)
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
                           timestamp_basis=frame.timestamp_basis, shape=frame.image.shape[:2])
            sys.stdout.buffer.write(json.dumps(row).encode()+b"\n")
            if frame is not None:
                sys.stdout.buffer.write(frame.image.tobytes())
            sys.stdout.buffer.flush()
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Decoder exceptions may include source URL credentials.
        print("Video decoder failed", file=sys.stderr)
        raise SystemExit(1)
