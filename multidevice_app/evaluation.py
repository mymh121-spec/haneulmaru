"""Validate inputs in Python; compute assignments and all metrics in Rust."""
import json
from .replay import finite, read_jsonl
from .rust_host import binary
from .worker import PipeWorker
from .contracts import SetupError


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


def request(payload):
    encoded = json.dumps(payload, allow_nan=False, ensure_ascii=True).encode()+b"\n"
    if len(encoded)>64*1024*1024:
        raise ValueError("Evaluation request exceeds 64 MiB; split the recording")
    worker = PipeWorker([binary(), "evaluate"], max_response_bytes=64*1024*1024)
    try:
        try:
            response = json.loads(worker.exchange(encoded, 60, "Rust evaluation"))
            if response.get("ok") is False:
                raise ValueError(response.get("error", "Invalid evaluation input"))
            if response.get("ok") is not True or "data" not in response:
                raise SetupError("Invalid Rust evaluation response")
            return response["data"]
        except (json.JSONDecodeError, AttributeError) as exc:
            raise SetupError("Invalid Rust evaluation response") from exc
    finally:
        worker.close()


def match(reference, predictions, threshold):
    return [tuple(pair) for pair in request(dict(operation="match",actual=reference,
                                                predicted=predictions,threshold=threshold))]


def evaluate(reference_rows, prediction_rows, threshold=.5, warmup=0):
    if not finite(threshold) or not 0 < threshold <= 1 or type(warmup) is not int or warmup < 0:
        raise ValueError("Invalid IoU threshold or warmup frame count")
    reference, predictions = frame_index(reference_rows), frame_index(prediction_rows)
    if not reference:
        raise ValueError("Reference must contain frames, including empty frames")
    def compact(index, session):
        frames=[]
        for key, row in sorted(index.items()):
            engine=row.get("engine", {})
            frames.append(dict(frame_sequence=key[1],replay_pass=key[0],
                tracks=[{k:t.get(k) for k in ("code","xyxy","id")} for t in row["tracks"]],
                session_id=row.get("session_id",session),
                frame_timestamp_ms=row.get("frame_timestamp_ms",row.get("time_ms")),
                image_size_px=row.get("image_size_px"),inference_ms=engine.get("inference_ms"),
                processing_ms=row.get("processing_ms"),
                model_hash=engine.get("model_verification",{}).get("model_sha256")))
        return frames
    return request(dict(operation="evaluate",reference=compact(reference,"reference"),
                        predictions=compact(predictions,"prediction"),threshold=threshold,warmup=warmup))


def evaluate_files(reference, predictions, **kwargs):
    return evaluate(read_jsonl(reference), read_jsonl(predictions), **kwargs)
