"""Exercise the actual Rust video executable with a deterministic 60 Hz recording."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from .evaluation import evaluate
from .model_profile import model_sha256
from .rust_host import ROOT
from .verify_60fps import make_recording, distribution, PERIOD_MS


def executable():
    override = os.environ.get("HANEULMARU_NATIVE_VIDEO")
    return Path(override).resolve() if override else ROOT / "native_video/target/release" / (
        "haneulmaru-video.exe" if os.name == "nt" else "haneulmaru-video")


def command(source, *options):
    config = [] if "--config" in options else ["--config", str(ROOT / "demo/config.json")]
    return [str(executable()), *config, "--source", str(source), *map(str, options)]


def run(count=600, width=512, height=512, paced=False):
    if count < 48 or min(width, height) < 32:
        raise ValueError("Use at least 48 frames and dimensions >=32")
    with tempfile.TemporaryDirectory(prefix="native-video-60fps-") as temporary:
        clip = make_recording(temporary, count, width, height)
        started = time.perf_counter()
        packets, arrivals = [], []
        with subprocess.Popen(command(clip, "--max-fps", 60 if paced else 0),
                              cwd=ROOT.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            timer = threading.Timer(45 + count / 30, child.kill)
            timer.start()
            try:
                for line in child.stdout:
                    arrivals.append(time.perf_counter())
                    packets.append(json.loads(line))
                stderr = child.stderr.read().decode("utf-8", errors="replace")
                if child.wait() != 0:
                    raise AssertionError(f"Rust video failed: {stderr}")
            finally:
                timer.cancel()
                if child.poll() is None:
                    child.kill()
        elapsed = time.perf_counter() - started
    assert len(packets) == count, (len(packets), count)
    for i, packet in enumerate(packets):
        assert packet["frame_sequence"] == i + 1
        assert abs(packet["frame_timestamp_ms"] - i * PERIOD_MS) < .01
        assert packet["image_size_px"] == [width, height]
        assert packet["host_backend"] == "rust" and packet["input_backend"] == "rust_opencv"
        assert packet["recovery_count"] == 0
        assert not packet["imu"]["valid"]  # Recording has no synchronized IMU.
    assert len({p["session_id"] for p in packets}) == 1
    assert len(packets[0]["tracks"]) == 3 and len(packets[47]["tracks"]) == 4
    assert all(len(p["tracks"]) <= 4 for p in packets)
    assert all(p["tracks"] == packets[47]["tracks"] for p in packets[48:])
    reference = None
    if (width, height) == (512, 512):
        original = [json.loads(line) for line in (ROOT / "demo/expected-tracks.jsonl").read_text().splitlines()]
        expected = [dict(frame_sequence=i+1, frame_timestamp_ms=i*PERIOD_MS,
                         tracks=original[min(i, 47)]["tracks"]) for i in range(count)]
        reference = evaluate(expected, packets)["totals"]
        assert all(reference[k] == 0 for k in ("fp", "fn", "id_switches", "matched_without_id")), reference
    service = [p["host_service_ms"] for p in packets[10:]]
    return dict(schema_version=1, frames=count, dimensions=[width, height], source_fps=60,
                max_fps=60 if paced else 0, functional_pass=True, sequence_gaps=0,
                unexpected_session_resets=0, reference_512=reference,
                model="synthetic-peaks-128-input", model_sha256=model_sha256(ROOT / "demo/synthetic-peaks.onnx"),
                wall_seconds=elapsed, startup_included_fps=count/elapsed, warmup_excluded_frames=10,
                steady_output_fps=(count-10)/(arrivals[-1]-arrivals[9]),
                steady_service_ms=distribution(service),
                steady_decode_ms=distribution([p["input_read_ms"] for p in packets[10:]]),
                steady_pipeline_ms=distribution([p["processing_ms"] for p in packets[10:]]),
                steady_over_16_67ms=sum(t > PERIOD_MS for t in service),
                scope="Rust OpenCV decode + Rust ONNX inference/tracking + Rust JSONL stdout; output FPS includes IPC/output and pacing; service excludes output serialization/write and pacing; no hardware, display, trained model or recorded IMU")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--paced", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.frames, args.width, args.height, args.paced)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as out:
            out.write(text)
    print(text, end="")
