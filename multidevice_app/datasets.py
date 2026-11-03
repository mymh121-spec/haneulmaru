"""Import bounding-box datasets for YOLO training and Rust inference."""
import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import tempfile

import yaml
from .contracts import SetupError
from .model_profile import validate_classes

IMAGE_TYPES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
SPLITS = ("train", "val", "test")


@dataclass
class Sample:
    image: Path
    boxes: list[tuple[int, float, float, float, float]]


def yolo_names(names):
    if isinstance(names, dict):
        by_id = {}
        for key, value in names.items():
            if type(key) not in (int, str) or not str(key).isdecimal() or int(key) in by_id:
                raise ValueError(f"Invalid YOLO class ID: {key}")
            by_id[int(key)] = value
        if set(by_id) != set(range(len(by_id))):
            raise ValueError("YOLO class IDs must be contiguous from 0")
        names = [by_id[i] for i in range(len(by_id))]
    return validate_classes(names)


def image_paths(entry, root):
    entries = entry if isinstance(entry, list) else [entry]
    for item in entries:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Expected a local image directory, image or .txt image list")
        path = (root / item).resolve()
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_TYPES)
        elif path.is_file() and path.suffix.lower() == ".txt":
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    yield (path.parent / line.strip()).resolve()
        elif path.is_file() and path.suffix.lower() in IMAGE_TYPES:
            yield path
        else:
            raise ValueError(f"Missing local dataset input: {path}")


def yolo_samples(entry, root, count):
    for image in image_paths(entry, root):
        if not image.is_file() or image.suffix.lower() not in IMAGE_TYPES:
            raise ValueError(f"Invalid image path: {image}")
        parts = list(image.parts)
        # Standard images/... -> labels/... convention; flat datasets use adjacent txt files.
        if "images" in parts:
            parts[len(parts)-1-parts[::-1].index("images")] = "labels"
        label = Path(*parts).with_suffix(".txt")
        rows = []
        if label.is_file():
            for line_no, line in enumerate(label.read_text(encoding="utf-8-sig").splitlines(), 1):
                fields = line.split()
                if not fields:
                    continue
                if len(fields) != 5 or not fields[0].isdecimal() or int(fields[0]) >= count:
                    raise ValueError(f"{label}:{line_no}: expected class_id cx cy w h")
                x, y, w, h = map(float, fields[1:])
                if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y, w, h)) or min(w,h) <= 0:
                    raise ValueError(f"{label}:{line_no}: invalid normalized box")
                rows.append((int(fields[0]), x, y, w, h))
        yield Sample(image, rows)


def read_yolo(path):
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not {"train","val","names"} <= data.keys():
        raise ValueError(f"{path}: train, val and names are required")
    names = yolo_names(data["names"])
    if "nc" in data and data["nc"] != len(names):
        raise ValueError("nc differs from the number of class names")
    root = (path.parent / (data.get("path") or ".")).resolve()
    return names, {s: yolo_samples(data[s], root, len(names)) for s in SPLITS if data.get(s) is not None}


def coco_samples(data, root, category_ids):
    images = {}
    for img in data["images"]:
        image_id = img["id"]
        if image_id in images:
            raise ValueError(f"Duplicate COCO image ID: {image_id}")
        w, h = img["width"], img["height"]
        if type(w) is not int or type(h) is not int or min(w,h) <= 0:
            raise ValueError(f"Invalid image dimensions: {image_id}")
        path = (root / img["file_name"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in IMAGE_TYPES:
            raise ValueError(f"Invalid COCO image path: {path}")
        images[image_id] = (Sample(path, []), w, h)
    for ann in data["annotations"]:
        if ann.get("iscrowd", 0):
            continue
        if ann["image_id"] not in images or ann["category_id"] not in category_ids:
            raise ValueError(f"Unknown COCO image/category ID in annotation {ann.get('id')}")
        rec, width, height = images[ann["image_id"]]
        x, y, w, h = ann["bbox"]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (x, y, w, h)) or min(w, h) <= 0:
            raise ValueError(f"Invalid COCO bbox: {ann['bbox']}")
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(width, x + w), min(height, y + h)
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"COCO bbox outside image: {ann['bbox']}")
        cls = category_ids[ann["category_id"]]
        rec.boxes.append((cls, (x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
                          (x2 - x1) / width, (y2 - y1) / height))
    return (record for record, _, _ in images.values())


def read_coco(inputs):
    if not {"train", "val"} <= inputs.keys() <= set(SPLITS):
        raise ValueError("Expected train, val and optionally test COCO splits")
    names, splits = None, {}
    for split in SPLITS:
        if split not in inputs:
            continue
        ann_path, root = inputs[split]
        data = json.loads(Path(ann_path).read_text(encoding="utf-8-sig"))
        categories = sorted(data["categories"], key=lambda c:c["id"])
        ids = [c["id"] for c in categories]
        labels = validate_classes([c["name"] for c in categories])
        if len(ids) != len(set(ids)) or any(type(i) is not int for i in ids):
            raise ValueError("COCO category IDs must be unique integers")
        if names is None:
            names = labels
        index = {label:i for i,label in enumerate(names)}
        if not set(labels) <= set(index):
            raise ValueError(f"{split}: category absent from training categories")
        remap = {c["id"]:index[c["name"]] for c in categories}
        splits[split] = coco_samples(data, Path(root).resolve(), remap)
    return names, splits


def label_line(row, class_count, source):
    cls, cx, cy, w, h = row
    if type(cls) is not int or not 0 <= cls < class_count or not all(
        math.isfinite(v) and 0 <= v <= 1 for v in (cx, cy, w, h)
    ) or min(w, h) <= 0:
        raise ValueError(f"Invalid normalized detection box in {source}: {row}")
    return f"{cls} {cx:.10g} {cy:.10g} {w:.10g} {h:.10g}\n"


def write_dataset(output, names, splits, *, source_info=None):
    validate_classes(names)
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Choose a new output directory: {output}")
    if not {"train", "val"} <= splits.keys() <= set(SPLITS):
        raise ValueError("Expected train, val and optionally test splits")
    output.parent.mkdir(parents=True, exist_ok=True)
    counts, seen = {}, set()
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".dataset-") as tmp:
        stage = Path(tmp) / "dataset"
        for split, samples in splits.items():
            images = stage / "images" / split
            labels = stage / "labels" / split
            images.mkdir(parents=True)
            labels.mkdir(parents=True)
            image_count = box_count = 0
            for rec in samples:
                source = rec.image.resolve()
                if source in seen:
                    raise ValueError(f"Image repeated in dataset: {source}")
                seen.add(source)
                image_count += 1
                stem = f"{image_count:07d}"
                shutil.copyfile(source, images / (stem + source.suffix.lower()))
                lines = [label_line(row, len(names), source) for row in rec.boxes]
                (labels / (stem + ".txt")).write_text("".join(lines), encoding="utf-8")
                box_count += len(lines)
            if not image_count or (split == "train" and not box_count):
                raise ValueError(f"{split}: empty split or no trainable boxes")
            counts[split] = {"images": image_count, "boxes": box_count}
        spec = {s: "images/" + s for s in splits}
        spec.update(nc=len(names), names=names)
        (stage / "data.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        profile = dict(schema_version=1, model_path="best.onnx", classes=names, input_size=640,
                       input_name="images", output_name="output0", output_layout="yolo_raw", agnostic_nms=False)
        (stage / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if source_info is not None:
            metadata = {**source_info, "classes": names, "counts": counts}
            (stage / "source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return counts


def main():
    parser = argparse.ArgumentParser(description="객체 탐지 데이터셋 → YOLO 학습 폴더 + Rust 모델 프로필")
    commands = parser.add_subparsers(dest="format", required=True)
    yolo = commands.add_parser("yolo")
    yolo.add_argument("--data", type=Path, required=True, help="YOLO data.yaml")
    coco = commands.add_parser("coco")
    for split in SPLITS:
        coco.add_argument("--" + split, nargs=2, metavar=("INSTANCES_JSON", "IMAGES_ROOT"), required=split != "test")
    remote = commands.add_parser("remote", help="Hugging Face 또는 JSON API에서 가져오기")
    remote.add_argument("--source", type=Path, required=True, help="원격 데이터 소스 JSON")
    remote.add_argument("--limit-per-split", type=int, help="분할별 최대 행 수 (시험용)")
    for sub in (yolo, coco, remote):
        sub.add_argument("--output", type=Path, required=True, help="새 출력 폴더")
    args = parser.parse_args()
    try:
        if args.format == "remote":
            from .remote_datasets import import_remote
            counts = import_remote(args.source, args.output, args.limit_per_split)
            print(json.dumps({"output": str(args.output.resolve()), "splits": counts}, ensure_ascii=False))
            return
        if args.format == "yolo":
            names, splits = read_yolo(args.data)
        else:
            names, splits = read_coco({s: getattr(args, s) for s in SPLITS if getattr(args, s)})
        counts = write_dataset(args.output, names, splits)
        print(json.dumps({"output": str(args.output.resolve()), "classes": names, "splits": counts}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, SetupError, yaml.YAMLError) as exc:
        parser.exit(1, f"데이터셋을 가져오지 못했습니다: {exc}\n")


if __name__ == "__main__":
    main()
