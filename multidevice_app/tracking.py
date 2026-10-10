from dataclasses import replace
from .contracts import Detection


class NoTracking:
    def update(self, frame, detections):
        return detections

    def reset(self):
        pass


class UltralyticsTracking:
    """Both engines feed the same pinned ByteTrack/BoT-SORT adapter."""
    def __init__(self, kind):
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

    def update(self, frame, detections):
        import numpy as np
        from ultralytics.engine.results import Boxes
        classes = {"R": 0, "B": 1, "G": 2, "UNKNOWN": 3}
        rows = np.asarray([[*d.xyxy, d.confidence, classes[d.code]] for d in detections],
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


def create_tracker(name):
    return NoTracking() if name == "none" else UltralyticsTracking(name)
