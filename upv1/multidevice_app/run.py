"""Windows/Linux host entry; all visual output requires explicit --show."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multidevice_app.engines import create_engine
from multidevice_app.inputs import NoImu, OpenCVSource
from multidevice_app.imu import DirectionClassifier
from multidevice_app.tracking import create_tracker
from multidevice_app.pipeline import Pipeline
from multidevice_app.contracts import SetupError


def validate_config(config):
    if config.get("schema_version") != 1:
        raise ValueError("config schema_version=1이 필요합니다")
    engine = config["engine"]
    if engine["name"] not in ("disabled", "ultralytics", "clikart"):
        raise SetupError("engine.name: disabled/ultralytics/clikart 중 선택하세요")
    for value in (engine["confidence"], engine["nms_iou"], config["color_confidence"],
                  config["reset_tracking_after_gap_ms"]):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SetupError("인식/추적 설정에는 유한한 숫자를 지정하세요")
    if type(config["camera"]["live"]) is not bool or type(config["imu"]["enabled"]) is not bool or type(config["imu"]["ble"]) is not bool:
        raise SetupError("live/enabled/ble 설정은 true 또는 false여야 합니다")
    if config["camera"]["rotation_clockwise_deg"] not in (0, 90, 180, 270):
        raise SetupError("회전 설정은 0/90/180/270도 중 선택하세요")
    if type(engine["input_size"]) is not int or engine["input_size"] < 32 or engine["input_size"] % 32:
        raise ValueError("input_size는 32의 양의 배수여야 합니다")
    if engine["classes"] != ["R", "B", "G"]:
        raise ValueError("classes는 R/B/G 순서여야 합니다")
    if not 0 < engine["confidence"] <= 0.1 or not engine["confidence"] <= config["color_confidence"] <= 1:
        raise ValueError("confidence <= 0.1; color_confidence >= confidence")
    if not 0 < engine["nms_iou"] <= 1 or config["reset_tracking_after_gap_ms"] <= 0:
        raise ValueError("잘못된 NMS/추적 시간 설정")
    if config["tracking"] not in ("none", "bytetrack", "botsort"):
        raise ValueError("tracking: none/bytetrack/botsort")
    if engine["name"] == "disabled" and config["tracking"] != "none":
        raise ValueError("인식 미연결 상태에서는 tracking=none을 사용하세요")


def draw(frame, packet):
    import cv2
    canvas = frame.image.copy()
    colors = {"R": (30, 30, 230), "B": (230, 90, 40), "G": (40, 200, 40), "UNKNOWN": (140, 140, 140)}
    for track in packet["tracks"]:
        x1, y1, x2, y2 = map(round, track["xyxy"])
        color = colors[track["code"]]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = f"{track['code']} | ID {track['id'] if track['id'] is not None else 'pending'}"
        cv2.putText(canvas, label, (x1, max(y1 - 8, 18)), cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
    imu = packet["imu"]
    direction = imu.get("direction") or ("idle" if imu["valid"] else "unavailable")
    cv2.putText(canvas, f"IMU: {direction.upper()}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (230, 230, 230), 2)
    engine = packet["engine"]
    text = f"Engine: {engine['name']} | {engine['inference_ms']:.1f} ms" if engine["ready"] else "Recognition engine not connected"
    cv2.putText(canvas, text, (12, 57), cv2.FONT_HERSHEY_SIMPLEX, .55, (230, 230, 230), 1)
    return canvas


def main():
    parser = argparse.ArgumentParser(description="S22 영상 + NU40 IMU + 교체 가능한 인식 엔진")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--source", help="S22 스트림 URL 또는 녹화 파일")
    parser.add_argument("--recording", action="store_true", help="녹화 영상: 현재 IMU와 연결하지 않음")
    parser.add_argument("--engine", choices=("disabled", "ultralytics", "clikart"))
    parser.add_argument("--model", help="설정 파일 기준 상대 경로 또는 절대 경로")
    parser.add_argument("--tracker", choices=("none", "bytetrack", "botsort"))
    parser.add_argument("--imu-port", help="예: COM5; 지정하지 않으면 NU40 자동 검색")
    parser.add_argument("--no-imu", action="store_true")
    parser.add_argument("--show", action="store_true", help="외부 영상 창 표시; Q로 종료")
    parser.add_argument("--jsonl", type=Path, help="선택: 결과를 새 파일에 저장")
    parser.add_argument("--flush-every-frame", action="store_true",
                        help="JSONL을 매 프레임 즉시 저장 (기본: 프레임 처리 중 0.5초 간격)")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    root = args.config.resolve().parent
    if args.source is not None:
        config["camera"]["source"] = args.source
    if args.recording:
        config["camera"]["live"] = False
    if Path(config["camera"]["source"]).is_file():
        config["camera"]["live"] = False
    if args.engine:
        config["engine"]["name"] = args.engine
    if args.model:
        config["engine"]["model_path"] = args.model
    if args.tracker:
        config["tracking"] = args.tracker
    if args.imu_port:
        config["imu"]["port"] = args.imu_port
    validate_config(config)
    if not config["camera"]["source"]:
        parser.error("--source로 S22 영상 입력 주소 또는 파일을 지정하세요")
    if args.jsonl and (args.jsonl.exists() or not args.jsonl.parent.is_dir()):
        parser.error("JSONL은 존재하는 폴더 안의 새 파일이어야 합니다")
    engine, camera, imu, pipeline, output = None, None, NoImu(), None, None
    try:
        engine = create_engine(config["engine"], root)
        tracker = create_tracker(config["tracking"])
        model = json.loads((root / config["imu"]["training_path"]).read_text(encoding="utf-8"))
        pipeline = Pipeline(engine, tracker, DirectionClassifier(model), config["color_confidence"],
                            config["reset_tracking_after_gap_ms"])
        camera = OpenCVSource(config["camera"]["source"], config["camera"]["rotation_clockwise_deg"])
        if config["imu"]["enabled"] and not args.no_imu and config["camera"]["live"]:
            from multidevice_app.nu40_transport import ImuHub
            options = config["imu"]
            imu = ImuHub(port=options["port"], baud=options["baud"], ble=options["ble"],
                         ble_address=options["ble_address"])
        imu.start()
        output = args.jsonl.open("x", encoding="utf-8") if args.jsonl else None
        print("입력 대기 중. 종료: Q(영상 창) 또는 Ctrl+C", file=sys.stderr)
        processed = 0
        last_flush = time.monotonic()
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
            # Window-only use needs no JSON serialization. File writes remain
            # incremental; flush periodically instead of on every video frame.
            if output or not args.show:
                line = json.dumps(packet, ensure_ascii=False, allow_nan=False)
                if output:
                    output.write(line + "\n")
                    now = time.monotonic()
                    if args.flush_every_frame or now - last_flush >= 0.5:
                        output.flush()
                        last_flush = now
                else:
                    print(line, flush=True)
            if args.show:
                import cv2
                cv2.imshow("Haneulmaru S22 + NU40", draw(frame, packet))
                if cv2.waitKey(1) & 0xff == ord("q"):
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
        print(f"실행 중단 ({type(exc).__name__}). 설정·입력·SDK를 확인하세요.", file=sys.stderr)
        raise SystemExit(1)
