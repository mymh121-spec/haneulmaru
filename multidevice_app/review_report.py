"""Generate a self-contained, offline HTML review artifact; never upload video."""
import json
from pathlib import Path
from .evaluation import frame_index
from .replay import read_jsonl, finite


def write_report(source, output):
    rows = list(read_jsonl(source))
    frame_index(rows)
    if not rows:
        raise ValueError("No frames to review")
    for row in rows:
        size = row.get("image_size_px")
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
            raise ValueError("Review needs image_size_px on every frame")
        if not finite(row.get("frame_timestamp_ms")) or row["frame_timestamp_ms"] < 0:
            raise ValueError("Review needs frame_timestamp_ms")
    data = json.dumps(rows, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("review_template.html").read_text(encoding="utf-8")
    with Path(output).open("x", encoding="utf-8") as stream:
        stream.write(template.replace("__FRAME_DATA__", data))
