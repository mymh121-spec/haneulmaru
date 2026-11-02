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

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multidevice_app.engines import create_engine
from multidevice_app.inputs import NoImu, OpenCVSource
from multidevice_app.imu import DirectionClassifier
from multidevice_app.tracking import create_tracker
from multidevice_app.pipeline import Pipeline
from multidevice_app.contracts import SetupError
from multidevice_app.model_profile import load_profile, validate_classes, detector_options


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
        label = f"{track['code']} | ID {track['id'] if track['id'] is not None else 'pending'} | {track['confidence']:.0%}"
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
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    root = args.config.resolve().parent
    if args.profile:
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
    if not config["camera"]["source"]:
        parser.error("--source로 S22 영상 입력 주소 또는 파일을 지정하세요")
    if args.jsonl and (args.jsonl.exists() or not args.jsonl.parent.is_dir()):
        parser.error("JSONL은 존재하는 폴더 안의 새 파일이어야 합니다")
    engine, camera, imu, pipeline, output = None, None, NoImu(), None, None
    try:
        engine = create_engine(config["engine"], root)
        tracker = create_tracker(config["tracking"], config["engine"]["classes"])
        model = json.loads((root / config["imu"]["training_path"]).read_text(encoding="utf-8"))
        pipeline = Pipeline(engine, tracker, DirectionClassifier(model), config["color_confidence"],
                            config["reset_tracking_after_gap_ms"])
        camera = OpenCVSource(config["camera"]["source"], config["camera"]["rotation_clockwise_deg"],
                              recording=not config["camera"]["live"])
        if config["imu"]["enabled"] and not args.no_imu and config["camera"]["live"]:
            from multidevice_app.rust_host import WifiImu
            imu = WifiImu(config["imu"].get("bind", "0.0.0.0:8793"), root / config["imu"]["training_path"])
        imu.start()
        output = args.jsonl.open("x", encoding="utf-8") if args.jsonl else None
        print("입력 대기 중. 종료: Q(영상 창) 또는 Ctrl+C", file=sys.stderr)
        processed = 0
        playback_session, playback_origin = None, None
        while True:
            frame = camera.read()
            if frame is None:
                if not processed:
                    raise SetupError("읽을 수 있는 영상 프레임이 없습니다. S22 입력 주소나 파일을 확인하세요")
                if config["camera"]["live"]:
                    raise SetupError("실시간 영상 연결이 끊겼습니다. 입력 주소를 확인하고 다시 실행하세요")
                break
            packet = pipeline.process(frame, imu.snapshot(), attach_live_imu=config["camera"]["live"])
            processed += 1
            # This is arrival/decode association, explicitly NOT calibrated
            # exposure-time synchronization of S22 camera and board clocks.
            packet["imu_alignment"] = "host_arrival_approximate" if config["camera"]["live"] else "unavailable"
            line = json.dumps(packet, ensure_ascii=False, allow_nan=False)
            if output:
                output.write(line + "\n")
                output.flush()
            elif not args.show:
                print(line, flush=True)
            if args.show:
                import cv2
                delay = 1
                if not config["camera"]["live"]:
                    # Pace file previews by video time; headless JSONL runs at full speed.
                    now = time.monotonic() * 1000
                    if playback_session != packet["session_id"]:
                        playback_session, playback_origin = packet["session_id"], now - frame.timestamp_ms
                    delay = max(1, round(playback_origin + frame.timestamp_ms - now))
                cv2.imshow("Haneulmaru S22 + NU40", draw(frame, packet))
                if cv2.waitKey(delay) & 0xff == ord("q"):
                    break
    finally:
        imu.stop()
        if camera:
            camera.close()
        if pipeline:
            pipeline.close()
        elif engine:
            engine.close()
        if output:
            output.close()
        if args.show and camera is not None:
            import cv2
            cv2.destroyAllWindows()


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
