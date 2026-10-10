"""Convert this dataset to coloured-drone YOLO boxes without changing originals."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

COLOR_IDS = {"red": 0, "blue": 1, "green": 2}


def prepare(source: Path, output: Path) -> Path:
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    if not (source / "images").is_dir():
        raise ValueError("학습 사진이 분리돼 있습니다. 먼저 restore_training_data.py로 원본 ZIP에서 복원하세요.")
    rows = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    selected, groups, seen = [], {}, set()
    for row in rows:
        if row["marker_color"] == "none":
            continue
        color, split = row["marker_color"], row["split"]
        if color not in COLOR_IDS or split not in ("train", "val", "test"):
            raise ValueError("Unexpected colour or split in manifest")
        group = row["source_id"]
        if group in groups and groups[group] != split:
            raise ValueError(f"Source photo occurs in multiple splits: {group}")
        groups[group] = split
        image = (source / row["image"]).resolve()
        if not image.is_relative_to(source) or not image.is_file():
            raise ValueError(f"Image missing or outside dataset: {row['image']}")
        if hashlib.sha256(image.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Image differs from manifest: {image.name}")
        key = (split, image.stem)
        if key in seen:
            raise ValueError(f"Duplicate output image: {image.name}")
        seen.add(key)
        width, height = float(row["width"]), float(row["height"])
        x, y, w, h = map(float, row["bbox"])
        if not all(math.isfinite(v) for v in (width, height, x, y, w, h)):
            raise ValueError("Non-finite image size or box")
        if min(width, height, w, h) <= 0 or x < 0 or y < 0 or x+w > width or y+h > height:
            raise ValueError(f"Invalid drone box: {image.name}")
        box = ((x+w/2)/width, (y+h/2)/height, w/width, h/height)
        selected.append((image, split, COLOR_IDS[color], box))
    counts = Counter(split for _, split, _, _ in selected)
    if any(counts[split] == 0 for split in ("train", "val", "test")):
        raise ValueError("All three dataset splits must contain images")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-rgb-", dir=output.parent))
    try:
        for split in ("train", "val", "test"):
            (temporary / "images" / split).mkdir(parents=True)
            (temporary / "labels" / split).mkdir(parents=True)
        for image, split, class_id, box in selected:
            shutil.copy2(image, temporary / "images" / split / image.name)
            label = temporary / "labels" / split / (image.stem + ".txt")
            label.write_text(str(class_id) + " " + " ".join(f"{v:.8f}" for v in box) + "\n", encoding="utf-8")
        # No 'path: .' entry: Ultralytics uses this YAML file's directory as root.
        (temporary / "data.yaml").write_text(
            "train: images/train\nval: images/val\ntest: images/test\nnc: 3\nnames:\n  0: R\n  1: B\n  2: G\n",
            encoding="utf-8",
        )
        report = {"images": len(selected), "splits": dict(counts), "classes": {"0": "R", "1": "B", "2": "G"},
                  "box_target": "whole_drone", "none_excluded": True,
                  "source_groups": len(groups), "manifest_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()}
        (temporary / "conversion.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return output / "data.yaml"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="원본 제외, 드론 전체 박스를 R/B/G 3클래스로 변환")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "yolo_rgb")
    args = parser.parse_args()
    try:
        print("YOLO dataset:", prepare(args.source, args.output))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"오류: {exc}\n")
