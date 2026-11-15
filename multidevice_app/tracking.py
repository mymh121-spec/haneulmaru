from dataclasses import replace
import json
from .contracts import SetupError
from .rust_host import binary
from .worker import PipeWorker, timeout_seconds


class NoTracking:
    def update(self, frame, detections):
        return detections

    def reset(self):
        pass


class RustTracking:
    def __init__(self, request_timeout_s=5, class_change_policy="allow"):
        if class_change_policy not in ("allow", "strict"):
            raise SetupError("class_change_policy: allow / strict")
        self.class_change_policy = class_change_policy
        self.proc = None
        self.worker = None
        self.request_timeout = timeout_seconds(request_timeout_s)

    def update(self, frame, detections):
        message = {"timestamp_ms": frame.timestamp_ms, "detections": [d.packet() for d in detections]}
        payload = json.dumps(message, allow_nan=False).encode() + b"\n"
        if self.worker is None:
            args = [binary(), "track"]
            if self.class_change_policy == "strict":
                args.append("strict")
            self.worker = PipeWorker(args)
            self.proc = self.worker.proc
        try:
            line = self.worker.exchange(payload, self.request_timeout, "Rust tracking")
            ids = json.loads(line)
            if (not isinstance(ids, list) or len(ids) != len(detections) or
                    any(i is not None and (type(i) is not int or i <= 0) for i in ids) or
                    len({i for i in ids if i is not None}) != sum(i is not None for i in ids)):
                raise SetupError("Rust 추적 결과의 ID 또는 개수가 잘못되었습니다.")
            return [replace(d, id=track_id) for d, track_id in zip(detections, ids)]
        except (SetupError, ValueError, TypeError) as exc:
            self.reset()
            if isinstance(exc, SetupError):
                raise
            raise SetupError("Rust tracking returned an invalid response") from exc

    def reset(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None
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


def create_tracker(name, classes=("R", "B", "G"), class_change_policy="allow"):
    if class_change_policy not in ("allow", "strict"):
        raise SetupError("class_change_policy: allow / strict")
    if name == "rust":
        return RustTracking(class_change_policy=class_change_policy)
    if name != "none" and class_change_policy == "strict":
        raise SetupError("strict 클래스 연결 정책에는 Rust 추적기를 사용하세요.")
    return NoTracking() if name == "none" else UltralyticsTracking(name, classes)
