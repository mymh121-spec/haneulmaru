"""Windows/Linux host entry; all visual output requires explicit --show."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time
import zlib
from functools import lru_cache
import os
from contextlib import ExitStack

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multidevice_app.engines import create_engine
from multidevice_app.inputs import NoImu, OpenCVSource
from multidevice_app.imu import DirectionClassifier
from multidevice_app.tracking import create_tracker
from multidevice_app.pipeline import Pipeline
from multidevice_app.contracts import SetupError
from multidevice_app.model_profile import load_profile, validate_classes, detector_options, profile_metadata
from multidevice_app.replay import ImuReplay, DetectionReplay, CachingEngine, finite
from multidevice_app.recovery import Budget, RecoveringPipeline, RecoveringSource


def validate_config(config):
    if config.get("schema_version") != 1:
        raise ValueError("config schema_version=1이 필요합니다")
    engine = config["engine"]
    if engine["name"] not in ("disabled", "ultralytics", "rust"):
        raise SetupError("engine.name: disabled/ultralytics/rust 중 선택하세요")
    for value in (engine["confidence"], engine["nms_iou"], config["color_confidence"],
                  config["reset_tracking_after_gap_ms"]):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SetupError("인식/추적 설정에는 유한한 숫자를 지정하세요")
    if type(config["camera"]["live"]) is not bool or type(config["imu"]["enabled"]) is not bool:
        raise SetupError("live/enabled 설정은 true 또는 false여야 합니다")
    if config["camera"]["rotation_clockwise_deg"] not in (0, 90, 180, 270):
        raise SetupError("회전 설정은 0/90/180/270도 중 선택하세요")
    if type(engine["input_size"]) is not int or not 32 <= engine["input_size"] <= 2048 or engine["input_size"] % 32:
        raise ValueError("input_size는 32~2048 범위의 32 배수여야 합니다")
    validate_classes(engine["classes"])
    profile_metadata(engine)
    if engine["name"] == "rust":
        detector_options(engine)
    if not 0 < engine["confidence"] <= 1 or not 0 <= config["color_confidence"] <= 1:
        raise ValueError("confidence와 표시 임계값은 0~1 범위여야 합니다")
    if not 0 < engine["nms_iou"] <= 1 or config["reset_tracking_after_gap_ms"] <= 0:
        raise ValueError("잘못된 NMS/추적 시간 설정")
    if config["tracking"] not in ("none", "rust", "bytetrack", "botsort"):
        raise ValueError("tracking: none/rust/bytetrack/botsort")
    if engine["name"] == "disabled" and config["tracking"] != "none":
        raise ValueError("인식 미연결 상태에서는 tracking=none을 사용하세요")


@lru_cache(maxsize=1)
def label_font():
    from PIL import ImageFont
    for path in (os.environ.get("HANEULMARU_FONT"), "malgun.ttf", "AppleSDGothicNeo.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"):
        if path:
            try:
                return ImageFont.truetype(path, 18)
            except OSError:
                continue
    raise SetupError("클래스 이름을 표시할 글꼴 경로를 HANEULMARU_FONT에 지정하세요.")


def draw(frame, packet):
    import cv2
    canvas = frame.image.copy()
    colors = {"R": (30, 30, 230), "B": (230, 90, 40), "G": (40, 200, 40), "UNKNOWN": (140, 140, 140)}
    palette = [(60, 190, 250), (224, 160, 84), (164, 100, 240), (188, 221, 72), (180, 198, 220)]
    unicode_labels = []
    for track in packet["tracks"]:
        x1, y1, x2, y2 = map(round, track["xyxy"])
        code = track["code"]
        color = colors.get(code, palette[zlib.crc32(code.encode("utf-8")) % len(palette)])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = f"{track.get('display_name', track['code'])} | ID {track['id'] if track['id'] is not None else 'pending'} | {track['confidence']:.0%}"
        if label.isascii():
            cv2.putText(canvas, label, (x1, max(y1 - 8, 18)), cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
        else:
            unicode_labels.append(((x1, max(y1-24, 0)), label, color[::-1]))
    if unicode_labels:
        from PIL import Image, ImageDraw
        import numpy as np
        image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        painter = ImageDraw.Draw(image)
        for position, label, rgb in unicode_labels:
            painter.text(position, label, fill=rgb, font=label_font())
        canvas = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    imu = packet["imu"]
    direction = imu.get("direction") or ("idle" if imu["valid"] else "unavailable")
    cv2.putText(canvas, f"IMU: {direction.upper()}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (230, 230, 230), 2)
    engine = packet["engine"]
    text = f"Engine: {engine['name']} | {engine['inference_ms']:.1f} ms" if engine["ready"] else "Recognition engine not connected"
    cv2.putText(canvas, text, (12, 57), cv2.FONT_HERSHEY_SIMPLEX, .55, (230, 230, 230), 1)
    counts = f"Objects: {len(packet['tracks'])} | With ID: {sum(t['id'] is not None for t in packet['tracks'])}"
    cv2.putText(canvas, counts, (12, 80), cv2.FONT_HERSHEY_SIMPLEX, .55, (230, 230, 230), 1)
    return canvas


def main():
    parser = argparse.ArgumentParser(description="S22 영상 + NU40 IMU + 교체 가능한 인식 엔진")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--profile", type=Path, help="학습 데이터셋에 대응하는 모델 프로필 JSON")
    parser.add_argument("--source", help="S22 스트림 URL 또는 녹화 파일")
    parser.add_argument("--recording", action="store_true", help="녹화 영상: 현재 IMU와 연결하지 않음")
    parser.add_argument("--engine", choices=("disabled", "ultralytics", "rust"))
    parser.add_argument("--model", help="모델 프로필(없으면 설정 파일) 기준 상대 경로 또는 절대 경로")
    parser.add_argument("--tracker", choices=("none", "rust", "bytetrack", "botsort"))
    parser.add_argument("--imu-bind", help="센서 UDP 수신 주소 (기본 0.0.0.0:8793)")
    parser.add_argument("--no-imu", action="store_true")
    parser.add_argument("--show", action="store_true", help="외부 영상 창 표시; Q로 종료")
    parser.add_argument("--jsonl", type=Path, help="선택: 결과를 새 파일에 저장")
    parser.add_argument("--imu-log", type=Path, help="영상 시간축의 IMU JSONL 재생")
    parser.add_argument("--imu-offset-ms", type=float, default=0, help="센서 시각에 더할 영상 시간 오프셋")
    parser.add_argument("--detections", type=Path, help="모델 대신 저장된 원시 탐지 JSONL 사용")
    parser.add_argument("--cache-detections", type=Path, help="원시 탐지를 새 JSONL 파일로 저장")
    parser.add_argument("--start-ms", type=float, default=0)
    parser.add_argument("--end-ms", type=float)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--speed", type=float, default=1, help="녹화 미리보기 배속; headless는 최대 속도")
    parser.add_argument("--max-restarts", type=int, default=0, help="전체 실행의 입력/작업자 재시작 예산 (0..20)")
    parser.add_argument("--io-timeout", type=float, default=5, help="격리 영상 입력 열기/읽기 제한 초")
    parser.add_argument("--isolate-input", action="store_true", help="녹화 파일도 별도 디코더로 처리; 라이브는 항상 격리")
    parser.add_argument("--flush-every", type=int, default=1, help="JSONL flush 주기; 큰 값은 파일 출력 비용 감소")
    parser.add_argument("--coordinates", type=Path, help="방향 추정용 카메라 투영 설정 JSON")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    root = args.config.resolve().parent
    if args.profile:
        config["engine"].pop("deployment", None)
        config["engine"].update(load_profile(args.profile))
    if args.source is not None:
        config["camera"]["source"] = args.source
    if args.recording:
        config["camera"]["live"] = False
    if Path(config["camera"]["source"]).is_file():
        config["camera"]["live"] = False
    if args.engine:
        config["engine"]["name"] = args.engine
    if args.model:
        model_root = args.profile.resolve().parent if args.profile else root
        config["engine"]["model_path"] = str(model_root / args.model)
    if args.tracker:
        config["tracking"] = args.tracker
    elif args.engine == "disabled":
        config["tracking"] = "none"
    if args.imu_bind:
        config["imu"]["bind"] = args.imu_bind
    validate_config(config)
    live = config["camera"]["live"]
    if (not finite(args.start_ms) or args.start_ms < 0 or
            (args.end_ms is not None and (not finite(args.end_ms) or args.end_ms <= args.start_ms)) or
            not finite(args.speed) or args.speed <= 0 or not 1 <= args.repeat <= 100 or
            args.flush_every < 1 or not finite(args.io_timeout) or args.io_timeout <= 0):
        parser.error("잘못된 재생 구간, 배속, 반복, 출력 또는 시간 제한")
    if live and (args.imu_log or args.detections or args.start_ms or args.end_ms is not None or args.repeat != 1):
        parser.error("센서/탐지 재생과 구간 반복은 --recording에서만 사용하세요")
    if args.imu_log and args.no_imu:
        parser.error("--imu-log와 --no-imu는 함께 사용할 수 없습니다")
    if args.cache_detections and (args.repeat != 1 or args.max_restarts or args.detections):
        parser.error("탐지 캐시는 반복/재시작/기존 캐시 없이 생성하세요")
    budget = Budget(args.max_restarts)
    if not config["camera"]["source"]:
        parser.error("--source로 S22 영상 입력 주소 또는 파일을 지정하세요")
    for target in (args.jsonl, args.cache_detections):
        if target and (target.exists() or not target.parent.is_dir()):
            parser.error("JSONL은 존재하는 폴더 안의 새 파일이어야 합니다")
    if args.jsonl and args.cache_detections and args.jsonl.resolve() == args.cache_detections.resolve():
        parser.error("탐지 캐시와 결과 파일을 분리하세요")
    model = json.loads((root / config["imu"]["training_path"]).read_text(encoding="utf-8"))
    replay = ImuReplay(args.imu_log, args.imu_offset_ms) if args.imu_log else None
    detections = DetectionReplay(args.detections) if args.detections else None
    coordinates = None
    if args.coordinates:
        from multidevice_app.coordinates import CoordinateAdapter
        coordinates = CoordinateAdapter(model, json.loads(args.coordinates.read_text(encoding="utf-8")))
    camera, imu, pipeline, output, cache = None, NoImu(), None, None, None
    def pipeline_factory():
        if config["engine"]["name"] == "rust" and config["tracking"] == "rust" and detections is None:
            from multidevice_app.rust_pipeline import RustPipeline
            runner = RustPipeline(config["engine"], root, DirectionClassifier(model), config["color_confidence"],
                                  config["reset_tracking_after_gap_ms"], cache)
            if runner.verification["status"] == "unverified":
                print("모델 배포 검증 안 됨: 가중치 해시와 학습 클래스 순서 기록이 없습니다.", file=sys.stderr)
            return runner
        engine = detections if detections is not None else create_engine(config["engine"], root)
        tracker = None
        try:
            if cache:
                engine = CachingEngine(engine, cache)
            verification = getattr(engine, "model_verification", None)
            if verification and verification["status"] == "unverified":
                print("모델 배포 검증 안 됨: 가중치 해시와 학습 클래스 순서 기록이 없습니다.", file=sys.stderr)
            tracker = create_tracker(config["tracking"], config["engine"]["classes"],
                                     config["engine"].get("class_change_policy", "allow"))
            return Pipeline(engine, tracker, DirectionClassifier(model), config["color_confidence"],
                            config["reset_tracking_after_gap_ms"])
        except Exception:
            try:
                engine.close()
            finally:
                close_tracker = getattr(tracker, "close", None)
                if close_tracker:
                    close_tracker()
            raise
    def source_factory():
        if live or args.isolate_input:
            from multidevice_app.video_worker import IsolatedSource
            return IsolatedSource(config["camera"]["source"], config["camera"]["rotation_clockwise_deg"],
                                  not live, args.io_timeout)
        return OpenCVSource(config["camera"]["source"], config["camera"]["rotation_clockwise_deg"], recording=True)
    try:
        cache = args.cache_detections.open("x", encoding="utf-8") if args.cache_detections else None
        pipeline = RecoveringPipeline(pipeline_factory, budget)
        camera = RecoveringSource(source_factory, budget, live=live)
        if config["imu"]["enabled"] and not args.no_imu and live:
            from multidevice_app.rust_host import WifiImu
            imu = WifiImu(config["imu"].get("bind", "0.0.0.0:8793"), root / config["imu"]["training_path"])
        imu.start()
        output = args.jsonl.open("x", encoding="utf-8") if args.jsonl else None
        print("입력 대기 중. 종료: Q 또는 Ctrl+C, 미리보기 일시정지: Space", file=sys.stderr)
        processed = 0
        replay_pass, generation, pass_frames = 0, 0, 0
        playback_session, playback_origin = None, None
        while True:
            read_start = time.perf_counter()
            frame = camera.read()
            read_ms = (time.perf_counter() - read_start) * 1000
            if frame is not None and frame.timestamp_ms < args.start_ms:
                continue
            if frame is None or (not live and args.end_ms is not None and frame.timestamp_ms >= args.end_ms):
                if not pass_frames:
                    raise SetupError("읽을 수 있는 영상 프레임이 없습니다. S22 입력 주소나 파일을 확인하세요")
                if replay_pass + 1 < args.repeat:
                    camera.close()
                    pipeline.reset()
                    camera = RecoveringSource(source_factory, budget)
                    replay_pass += 1
                    pass_frames, generation = 0, 0
                    continue
                break
            if camera.generation != generation:
                pipeline.reset()
                generation = camera.generation
            state = replay.snapshot_at(frame.timestamp_ms) if replay else imu.snapshot()
            packet = pipeline.process(frame, state, attach_live_imu=live or replay is not None)
            packet["replay_pass"] = replay_pass
            packet["input_read_ms"] = read_ms
            if coordinates:
                packet["coordinates"] = coordinates.record(packet, state)
            processed += 1
            pass_frames += 1
            # This is arrival/decode association, explicitly NOT calibrated
            # exposure-time synchronization of S22 camera and board clocks.
            packet["imu_alignment"] = "recorded_video_clock" if replay else ("host_arrival_approximate" if live else "unavailable")
            if output:
                output.write(json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n")
                if processed % args.flush_every == 0:
                    output.flush()
            elif not args.show:
                print(json.dumps(packet, ensure_ascii=False, allow_nan=False), flush=True)
            if args.show:
                import cv2
                delay = 1
                if not live:
                    # Pace file previews by video time; headless JSONL runs at full speed.
                    now = time.perf_counter() * 1000
                    if playback_session != packet["session_id"]:
                        playback_session, playback_origin = packet["session_id"], now - frame.timestamp_ms / args.speed
                    delay = max(1, round(playback_origin + frame.timestamp_ms / args.speed - now))
                cv2.imshow("Haneulmaru S22 + NU40", draw(frame, packet))
                key = cv2.waitKey(min(delay, 2_147_483_647)) & 0xff
                if key == ord(" "):
                    paused = time.perf_counter()
                    key = cv2.waitKey(0) & 0xff
                    if playback_origin is not None:
                        playback_origin += (time.perf_counter() - paused) * 1000
                if key == ord("q"):
                    break
    finally:
        # Run every cleanup even if one worker fails to stop.
        with ExitStack() as cleanup:
            if args.show and camera is not None:
                import cv2
                cleanup.callback(cv2.destroyAllWindows)
            for resource in (cache, output, pipeline, camera):
                if resource is not None:
                    cleanup.callback(resource.close)
            cleanup.callback(imu.stop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Exception text from video libraries may contain a stream credential.
        print(f"실행 중단 ({type(exc).__name__}). 설정·입력·Rust 런타임을 확인하세요.", file=sys.stderr)
        raise SystemExit(1)
