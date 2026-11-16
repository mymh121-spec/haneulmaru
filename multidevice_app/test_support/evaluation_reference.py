# Frozen Python oracle for migration parity tests only.
"""Explicit IoU-threshold detection/identity metrics, not COCO mAP or HOTA."""
import math
from ..replay import finite, read_jsonl


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union else 0


def assignment(costs, columns):
    """Rectangular Hungarian assignment with one free unmatched column per row."""
    rows = len(costs)
    count = columns + rows
    u, v, owner, parent = [0.]*(rows+1), [0.]*(count+1), [0]*(count+1), [0]*(count+1)
    for row in range(1, rows+1):
        owner[0], j = row, 0
        used, slack = [False]*(count+1), [float("inf")]* (count+1)
        while True:
            used[j], r = True, owner[j]
            delta, next_j = float("inf"), 0
            for k in range(1, count+1):
                if used[k]:
                    continue
                cost = costs[r-1][k-1] if k <= columns else 0.
                reduced = cost - u[r] - v[k]
                if reduced < slack[k]:
                    slack[k], parent[k] = reduced, j
                if slack[k] < delta:
                    delta, next_j = slack[k], k
            for k in range(count+1):
                if used[k]:
                    u[owner[k]] += delta
                    v[k] -= delta
                else:
                    slack[k] -= delta
            j = next_j
            if owner[j] == 0:
                break
        while j:
            previous = parent[j]
            owner[j], j = owner[previous], previous
    return [(owner[c]-1, c-1) for c in range(1, columns+1) if owner[c]]


def match(reference, predictions, threshold):
    overlaps = [[iou(a["xyxy"], b["xyxy"]) for b in predictions] for a in reference]
    # Matching cardinality takes priority over overlap sum.
    reward = min(len(reference), len(predictions)) + 1
    costs = [[-reward-overlaps[r][p] if a["code"] == b["code"] and overlaps[r][p] >= threshold
              else 1e6 for p, b in enumerate(predictions)] for r, a in enumerate(reference)]
    return [(r, p, overlaps[r][p]) for r, p in assignment(costs, len(predictions)) if costs[r][p] < 0]


def frame_index(rows):
    indexed = {}
    for row in rows:
        number = row.get("frame_sequence", row.get("frame"))
        repeat = row.get("replay_pass", 0)
        if type(number) is not int or number < 0 or type(repeat) is not int or repeat < 0:
            raise ValueError("Each frame needs a nonnegative frame_sequence or frame")
        key = (repeat, number)
        if key in indexed:
            raise ValueError(f"Duplicate frame identity: {key}")
        if not isinstance(row.get("tracks"), list):
            raise ValueError("Each frame needs a tracks list")
        ids = set()
        for track in row["tracks"]:
            box, code, identity = track.get("xyxy"), track.get("code"), track.get("id")
            if (not isinstance(box, (list, tuple)) or len(box) != 4 or not all(finite(v) for v in box)
                    or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]
                    or not isinstance(code, str) or not code):
                raise ValueError("Invalid track box/class")
            if identity is not None:
                if type(identity) not in (int, str) or identity == "" or identity in ids:
                    raise ValueError("Track IDs must be unique within a frame")
                ids.add(identity)
        indexed[key] = row
    return indexed


def timing_summary(values):
    if not values:
        return dict(samples=0, mean_ms=None, p50_ms=None, p95_ms=None)
    values = sorted(values)
    percentile = lambda p: values[max(0, math.ceil(len(values)*p)-1)]
    return dict(samples=len(values), mean_ms=sum(values)/len(values), p50_ms=percentile(.5), p95_ms=percentile(.95))


def evaluate(reference_rows, prediction_rows, threshold=.5, warmup=0):
    if not finite(threshold) or not 0 < threshold <= 1 or type(warmup) is not int or warmup < 0:
        raise ValueError("Invalid IoU threshold or warmup frame count")
    reference, predictions = frame_index(reference_rows), frame_index(prediction_rows)
    if not reference:
        raise ValueError("Reference must contain frames, including empty frames")
    totals = dict(tp=0, fp=0, fn=0, id_switches=0, matched_without_id=0)
    classes, last_id, details, overlaps = {}, {}, [], []
    for key in sorted(reference.keys() | predictions.keys()):
        actual, predicted = reference.get(key, {}), predictions.get(key, {})
        if actual and predicted:
            a_time = actual.get("frame_timestamp_ms", actual.get("time_ms"))
            b_time = predicted.get("frame_timestamp_ms", predicted.get("time_ms"))
            if a_time is not None and b_time is not None and (
                    not finite(a_time) or not finite(b_time) or abs(a_time-b_time) > .01):
                raise ValueError(f"Frame timestamp mismatch: {key}")
            if (actual.get("image_size_px") is not None and predicted.get("image_size_px") is not None
                    and actual["image_size_px"] != predicted["image_size_px"]):
                raise ValueError(f"Frame dimension mismatch: {key}")
        a, b = actual.get("tracks", []), predicted.get("tracks", [])
        matches = match(a, b, threshold)
        matched_a, matched_b = {r for r, _, _ in matches}, {p for _, p, _ in matches}
        switches, pending = 0, 0
        for r, p, overlap in matches:
            overlaps.append(overlap)
            identity = (key[0], actual.get("session_id", "reference"), a[r].get("id"))
            next_id = (predicted.get("session_id", "prediction"), b[p].get("id"))
            if next_id[1] is None:
                pending += 1
            elif identity[2] is not None:
                switches += identity in last_id and last_id[identity] != next_id
                last_id[identity] = next_id
        row = dict(replay_pass=key[0], frame=key[1], tp=len(matches), fp=len(b)-len(matches),
                   fn=len(a)-len(matches), id_switches=switches, matched_without_id=pending)
        details.append(row)
        for field in totals:
            totals[field] += row[field]
        for r, track in enumerate(a):
            counts = classes.setdefault(track["code"], dict(tp=0, fp=0, fn=0))
            counts["tp" if r in matched_a else "fn"] += 1
        for p, track in enumerate(b):
            if p not in matched_b:
                classes.setdefault(track["code"], dict(tp=0, fp=0, fn=0))["fp"] += 1
    def rates(counts):
        tp, fp, fn = (counts[k] for k in ("tp", "fp", "fn"))
        return dict(counts, precision=tp/(tp+fp) if tp+fp else None,
                    recall=tp/(tp+fn) if tp+fn else None)
    timing = dict(inference=[], processing=[])
    for key in sorted(predictions)[warmup:]:
        row = predictions[key]
        for name, value in (("inference", row.get("engine", {}).get("inference_ms")),
                            ("processing", row.get("processing_ms"))):
            if value is not None:
                if not finite(value) or value < 0:
                    raise ValueError("Timing must be finite and nonnegative")
                timing[name].append(value)
    return dict(schema_version=1, metric="class_aware_iou_assignment", iou_threshold=threshold,
                model_hashes=sorted({row.get("engine", {}).get("model_verification", {}).get("model_sha256")
                                    for row in predictions.values()
                                    if row.get("engine", {}).get("model_verification", {}).get("model_sha256")}),
                reference_frames=len(reference), predicted_frames=len(predictions),
                missing_frames=len(reference.keys()-predictions.keys()), extra_frames=len(predictions.keys()-reference.keys()),
                totals=rates(totals), mean_matched_iou=sum(overlaps)/len(overlaps) if overlaps else None,
                per_class={name: rates(counts) for name, counts in sorted(classes.items())},
                timing={name: timing_summary(values) for name, values in timing.items()},
                timing_warmup_frames=warmup, frames=details)


def evaluate_files(reference, predictions, **kwargs):
    return evaluate(read_jsonl(reference), read_jsonl(predictions), **kwargs)
