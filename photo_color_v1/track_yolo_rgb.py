"""YOLO coloured-drone detection + ByteTrack/BoT-SORT, without ground-truth boxes."""
from __future__ import annotations

import argparse
from collections import Counter, deque
import json
import math
from pathlib import Path
import sys


class RGBTracker:
    """One instance per video/camera. update() accepts an OpenCV BGR frame.

    Output: [{id, code, raw_code, confidence, xyxy}]. id=None means the detector
    found a box that has not yet been assigned a track. IDs are session-local.
    No manifest, labelled coordinates or file names are read during inference.
    """

    def __init__(self, weights, tracker="bytetrack", device="cpu", imgsz=640, conf=0.1, color_conf=0.4):
        if tracker not in ("bytetrack", "botsort"):
            raise ValueError("tracker must be bytetrack or botsort")
        if not 0 < conf <= 0.1 or not conf <= color_conf <= 1:
            raise ValueError("Use 0 < conf <= 0.1 and conf <= color_conf <= 1")
        weights = Path(weights)
        if not weights.is_file():
            raise FileNotFoundError(f"학습한 best.pt가 필요합니다: {weights}")
        from ultralytics import YOLO
        self.model = YOLO(str(weights.resolve()))
        if self.model.task != "detect" or self.model.names != {0: "R", 1: "B", 2: "G"}:
            raise ValueError("R/B/G 3클래스로 학습된 탐지 모델을 사용하세요. 기본 COCO 모델은 사용할 수 없습니다.")
        self.options = dict(tracker=tracker + ".yaml", device=device, imgsz=imgsz,
                            conf=conf, iou=0.7, agnostic_nms=True, verbose=False,
                            show=False, save=False, persist=True)
        self.color_conf = color_conf
        self.history = {}
        self.frame_number = 0

    def update(self, frame):
        self.frame_number += 1
        result = self.model.track(source=frame, **self.options)[0]
        boxes = result.boxes
        output = []
        if boxes is not None:
            ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)
            for xyxy, cls, score, track_id in zip(boxes.xyxy.cpu().tolist(), boxes.cls.int().cpu().tolist(), boxes.conf.cpu().tolist(), ids):
                raw_code = self.model.names[cls]
                code = raw_code if score >= self.color_conf else "UNKNOWN"
                if track_id is not None:
                    history, last_seen = self.history.get(track_id, (deque(maxlen=7), self.frame_number))
                    if self.frame_number - last_seen > 30:
                        history.clear()
                    history.append((raw_code, score) if score >= self.color_conf else ("UNKNOWN", 0.0))
                    votes = Counter()
                    for label, confidence in history:
                        if label != "UNKNOWN":
                            votes[label] += confidence
                    # Do not fill a currently uncertain observation with an old label.
                    if score >= self.color_conf and votes:
                        winner, strength = votes.most_common(1)[0]
                        code = winner if strength / sum(votes.values()) >= 0.65 else "UNKNOWN"
                    self.history[track_id] = history, self.frame_number
                output.append({"id": track_id, "code": code, "raw_code": raw_code,
                               "confidence": round(score, 4), "xyxy": [round(v, 2) for v in xyxy]})
        self.history = {key: value for key, value in self.history.items() if self.frame_number - value[1] <= 120}
        return output


def draw_tracks(frame, tracks):
    import cv2
    colors = {"R": (40, 40, 230), "B": (230, 80, 40), "G": (40, 200, 40), "UNKNOWN": (150, 150, 150)}
    canvas = frame.copy()
    for track in tracks:
        x1, y1, x2, y2 = map(round, track["xyxy"])
        color = colors[track["code"]]
        identity = str(track["id"]) if track["id"] is not None else "pending"
        label = f"ID {identity} | {track['code']} | {track['confidence']:.2f}"
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(canvas, label, (max(0, x1), max(20, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return canvas


def main():
    parser = argparse.ArgumentParser(description="YOLO R/B/G + ByteTrack 또는 BoT-SORT")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--source", required=True, help="영상 파일/스트림 URL 또는 카메라 번호 0")
    parser.add_argument("--tracker", choices=("bytetrack", "botsort"), default="bytetrack")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--color-conf", type=float, default=0.4)
    parser.add_argument("--show", action="store_true", help="외부 OpenCV 창 표시; Q로 종료")
    parser.add_argument("--save-video", type=Path, help="박스/ID/RGB가 표시된 새 mp4 파일")
    parser.add_argument("--jsonl", type=Path, help="프레임별 결과를 새 JSONL 파일에 저장")
    args = parser.parse_args()
    if args.imgsz < 32 or not 0.1 <= args.color_conf <= 1:
        parser.error("imgsz >= 32 and 0.1 <= color-conf <= 1 are required")
    for path in (args.save_video, args.jsonl):
        if path and (path.exists() or not path.parent.is_dir()):
            parser.error(f"새 파일 경로와 존재하는 상위 폴더를 지정하세요: {path}")
    if args.save_video and args.save_video.suffix.lower() != ".mp4":
        parser.error("--save-video requires a .mp4 path")
    if args.save_video and args.jsonl and args.save_video.resolve() == args.jsonl.resolve():
        parser.error("Video and JSONL output paths must differ")
    import cv2
    tracker = RGBTracker(args.weights, args.tracker, args.device, args.imgsz, color_conf=args.color_conf)
    source = int(args.source) if args.source.isdecimal() else args.source
    cap = cv2.VideoCapture(source)
    writer = None
    stream = None
    count = 0
    try:
        if not cap.isOpened():
            raise RuntimeError("영상/카메라를 열 수 없습니다.")
        stream = args.jsonl.open("x", encoding="utf-8") if args.jsonl else sys.stdout
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            fps = 30.0
        while True:
            ok, frame = cap.read()
            if not ok:
                if count == 0:
                    raise RuntimeError("읽을 수 있는 영상 프레임이 없습니다.")
                break
            count += 1
            tracks = tracker.update(frame)
            stream.write(json.dumps({"frame": count, "tracks": tracks}, ensure_ascii=False) + "\n")
            stream.flush()
            if args.show or args.save_video:
                canvas = draw_tracks(frame, tracks)
                if args.save_video:
                    if writer is None:
                        writer = cv2.VideoWriter(str(args.save_video), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                                 (canvas.shape[1], canvas.shape[0]))
                        if not writer.isOpened():
                            raise RuntimeError("출력 영상을 저장할 수 없습니다.")
                    writer.write(canvas)
                if args.show:
                    cv2.imshow("Haneulmaru RGB tracking", canvas)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if stream is not None and stream is not sys.stdout:
            stream.close()
        if args.show:
            cv2.destroyAllWindows()
    print(f"처리한 프레임: {count}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
