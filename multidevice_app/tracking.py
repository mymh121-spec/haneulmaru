from dataclasses import replace
import json
import subprocess
from .contracts import SetupError
from .rust_host import binary, close_worker


class NoTracking:
    def update(self, frame, detections):
        return detections

    def reset(self):
        pass


class RustTracking:
    def __init__(self):
        self.proc = None

    def update(self, frame, detections):
        if self.proc is None:
            self.proc = subprocess.Popen([binary(), "track"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        message = {"timestamp_ms": frame.timestamp_ms, "detections": [d.packet() for d in detections]}
        try:
            self.proc.stdin.write(json.dumps(message, allow_nan=False).encode() + b"\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                raise SetupError("Rust 추적기가 종료되었습니다. PC 콘솔의 오류를 확인하세요.")
            ids = json.loads(line)
            if not isinstance(ids, list) or len(ids) != len(detections):
                raise SetupError("Rust 추적 결과 개수가 탐지 결과와 다릅니다.")
            return [replace(d, id=track_id) for d, track_id in zip(detections, ids)]
        except OSError as exc:
            raise SetupError("Rust 추적 프로세스 연결이 끊겼습니다.") from exc

    def reset(self):
        if self.proc is not None:
            close_worker(self.proc)
            self.proc = None

    close = reset


class UltralyticsTracking:
    """Both engines feed the same pinned ByteTrack/BoT-SORT adapter."""
    def __init__(self, kind, classes=("R", "B", "G")):
        import ultralytics
        if ultralytics.__version__ != "8.4.63":
            raise RuntimeError("추적기 어댑터는 ultralytics==8.4.63을 사용합니다")
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.trackers.bot_sort import BOTSORT
        if kind not in ("bytetrack", "botsort"):
            raise ValueError("tracker: none / bytetrack / botsort")
        args = IterableSimpleNamespace(**YAML.load(check_yaml(kind + ".yaml")))
        # Do not download a second model when selecting BoT-SORT.
        if kind == "botsort":
            args.with_reid = False
        self.tracker = (BYTETracker if kind == "bytetrack" else BOTSORT)(args)
        self.class_ids = {name: i for i, name in enumerate([*classes, "UNKNOWN"])}

    def update(self, frame, detections):
        import numpy as np
        from ultralytics.engine.results import Boxes
        rows = np.asarray([[*d.xyxy, d.confidence, self.class_ids[d.code]] for d in detections],
                          dtype=np.float32).reshape(-1, 6)
        tracked = self.tracker.update(Boxes(rows, frame.image.shape[:2]), frame.image)
        by_index = {}
        for row in tracked:
            # Pinned API: x1,y1,x2,y2,id,score,class,detection_index.
            if len(row) != 8:
                raise RuntimeError("추적기 출력 계약이 변경되었습니다")
            by_index[int(row[7])] = int(row[4])
        # Keep pending detections too; do not fabricate a stable ID.
        return [replace(d, id=by_index.get(i)) for i, d in enumerate(detections)]

    def reset(self):
        self.tracker.reset()


def create_tracker(name, classes=("R", "B", "G")):
    if name == "rust":
        return RustTracking()
    return NoTracking() if name == "none" else UltralyticsTracking(name, classes)
