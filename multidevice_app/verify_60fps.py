"""60 Hz synthetic recording verification; timing is reported, not a CI speed gate."""
import argparse
import json
import math
from pathlib import Path
import tempfile
import time

import cv2

from .evaluation import evaluate
from .imu import DirectionClassifier
from .model_profile import model_sha256
from .inputs import OpenCVSource
from .replay import ImuReplay
from .rust_host import ROOT
from .rust_pipeline import RustPipeline
from .video_worker import IsolatedSource

FPS = 60
PERIOD_MS = 1000/FPS


def make_recording(directory, count, width=512, height=512):
    """48 motion frames at 60 Hz, then hold the last frame for sustained input."""
    path = Path(directory)/"input-60fps.avi"
    source = OpenCVSource(ROOT/"demo/multi-tracking.avi", recording=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"), FPS, (width,height))
    if not writer.isOpened():
        source.close()
        raise RuntimeError("FFV1 video writer is unavailable; cannot verify the recording")
    try:
        last = None
        for i in range(count):
            frame = source.read() if i<48 else None
            if frame is not None:
                last = cv2.resize(frame.image,(width,height),interpolation=cv2.INTER_NEAREST)
            if last is None:
                raise RuntimeError("Missing source demo frames")
            writer.write(last)
    finally:
        source.close();writer.release()
    return path


def imu_log(directory, count):
    path = Path(directory)/"imu.jsonl"
    # A delivery gap must expire at video time, even when processing is faster than real time.
    events = [dict(time_ms=t,session="synthetic-60fps",sample=dict(t_ms=t,gx=0,gy=10,gz=0))
              for t in range(0,math.ceil(count*PERIOD_MS)+50,50) if not 500<=t<1000]
    path.write_text(''.join(json.dumps(row)+'\n' for row in events),encoding="utf-8")
    return path


def distribution(values):
    ordered=sorted(values)
    return dict(mean=sum(values)/len(values),p50=ordered[math.ceil(.5*len(values))-1],
                p95=ordered[math.ceil(.95*len(values))-1],p99=ordered[math.ceil(.99*len(values))-1],max=max(values))


def run(count=600, width=512, height=512, isolated=False, paced=False):
    if count<48 or min(width,height)<32:
        raise ValueError("Use at least 48 frames and dimensions >=32")
    cfg=json.loads((ROOT/"demo/config.json").read_text(encoding="utf-8"))
    model=json.loads((ROOT/"assets/nu40-imu-direction-training.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="haneulmaru-60fps-") as temporary:
        clip=make_recording(temporary,count,width,height)
        replay=ImuReplay(imu_log(temporary,count))
        source=IsolatedSource(clip,recording=True,timeout_s=10) if isolated else OpenCVSource(clip,recording=True)
        pipeline=None
        packets, service, decode, release_lag, finish_lag = [],[],[],[],[]
        try:
            pipeline=RustPipeline(cfg["engine"],ROOT/"demo",DirectionClassifier(model))
            begin=time.perf_counter()
            with (Path(temporary)/"output.jsonl").open("w",encoding="utf-8") as output:
                for i in range(count):
                    scheduled=begin+i/FPS
                    if paced:
                        time.sleep(max(0,scheduled-time.perf_counter()))
                    started=time.perf_counter()
                    if paced:
                        release_lag.append(max(0,(started-scheduled)*1000))
                    frame=source.read()
                    decode.append((time.perf_counter()-started)*1000)
                    if frame is None:
                        raise AssertionError(f"Early EOF at frame {i+1}")
                    if frame.sequence!=i+1 or abs(frame.timestamp_ms-i*PERIOD_MS)>.01:
                        raise AssertionError("Frame identity or 60fps timestamp drift")
                    if frame.image.shape[:2]!=(height,width):
                        raise AssertionError("Unexpected decoded image size")
                    state=replay.snapshot_at(frame.timestamp_ms)
                    packet=pipeline.process(frame,state)
                    output.write(json.dumps(packet,ensure_ascii=False,allow_nan=False)+'\n')
                    output.flush()
                    ended=time.perf_counter()
                    service.append((ended-started)*1000)
                    if paced:
                        finish_lag.append(max(0,(ended-(scheduled+1/FPS))*1000))
                    packets.append(packet)
                elapsed=time.perf_counter()-begin
                if source.read() is not None:
                    raise AssertionError("Unexpected extra input frame")
        finally:
            try:
                source.close()
            finally:
                if pipeline is not None:
                    pipeline.close()
        if len({p["session_id"] for p in packets})!=1:
            raise AssertionError("Unexpected tracking session reset at 60fps")
        expired=[p["frame_sequence"] for p in packets if not p["imu"]["valid"]]
        expected_expired=[p["frame_sequence"] for p in packets if 800<p["frame_timestamp_ms"]<1000]
        if expired!=expected_expired:
            raise AssertionError("IMU expiry/recovery differs from the recorded 350ms policy")
        for p in packets[48:]:
            if p["tracks"]!=packets[47]["tracks"]:
                raise AssertionError("Stationary targets lost their IDs after the motion segment")
        if len(packets[0]["tracks"])!=3 or len(packets[47]["tracks"])!=4 or any(len(p["tracks"])>4 for p in packets):
            raise AssertionError("Synthetic background became detections, or endpoint targets are missing")
        exact_reference=None
        if (width,height)==(512,512):
            original=[json.loads(line) for line in (ROOT/"demo/expected-tracks.jsonl").read_text().splitlines()]
            reference=[dict(frame_sequence=i+1,frame_timestamp_ms=i*PERIOD_MS,
                            tracks=original[min(i,47)]["tracks"]) for i in range(count)]
            exact_reference=evaluate(reference,packets)["totals"]
            if any(exact_reference[k] for k in ("fp","fn","id_switches","matched_without_id")):
                raise AssertionError(f"60fps reference mismatch: {exact_reference}")
        steady=service[10:]
        return dict(schema_version=1,source_fps=FPS,frames=count,dimensions=[width,height],
                    decoder="isolated" if isolated else "direct",paced=paced,
                    model="synthetic-peaks-128-input",functional_pass=True,
                    model_sha256=model_sha256(ROOT/"demo/synthetic-peaks.onnx"),
                    object_counts=dict(min=min(len(p["tracks"]) for p in packets),
                                       max=max(len(p["tracks"]) for p in packets),
                                       total=sum(len(p["tracks"]) for p in packets)),
                    sequence_gaps=0,unexpected_session_resets=0,imu_expired_frames=expired,
                    reference_512=exact_reference,wall_seconds=elapsed,achieved_fps=count/elapsed,
                    all_frames_service_ms=distribution(service),warmup_excluded_frames=10,
                    steady_service_ms=distribution(steady),frame_budget_ms=PERIOD_MS,
                    steady_decode_ms=distribution(decode[10:]),
                    steady_inference_ms=distribution([p["engine"]["inference_ms"] for p in packets[10:]]),
                    steady_pipeline_ms=distribution([p["processing_ms"] for p in packets[10:]]),
                    steady_over_budget_frames=sum(v>PERIOD_MS for v in steady),
                    steady_capacity_fps=1000/(sum(steady)/len(steady)),
                    paced_finish_lag_ms=distribution(finish_lag) if paced else None,
                    paced_start_lateness_ms=distribution(release_lag) if paced else None,
                    paced_missed_deadlines=sum(v>0 for v in finish_lag) if paced else None,
                    scope="decode + recorded IMU + Rust inference/tracking + JSONL write/flush; no display/network/devices; startup excluded")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames",type=int,default=600)
    parser.add_argument("--width",type=int,default=512)
    parser.add_argument("--height",type=int,default=512)
    parser.add_argument("--isolated",action="store_true")
    parser.add_argument("--paced",action="store_true")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Output must be a new file")
    report=run(args.frames,args.width,args.height,args.isolated,args.paced)
    text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:
        with args.output.open("x",encoding="utf-8") as stream:
            stream.write(text)
    print(text,end="")


if __name__=="__main__":
    main()
