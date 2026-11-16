"""Decoded-pixel duplicates, bounded perceptual candidates and source-group leaks."""
import hashlib
from pathlib import Path

from PIL import Image


def fingerprints(path, perceptual=True):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    try:
        with Image.open(path) as image:
            image.load()
            rgb = image.convert("RGB")
            dimensions = image.size
            pixel_digest = hashlib.sha256(f"{rgb.width}x{rgb.height}:RGB:".encode())
            pixel_digest.update(rgb.tobytes())
            pixels = pixel_digest.hexdigest()
            difference = 0
            if perceptual:
                thumb = rgb.convert("L").resize((9, 8)).tobytes()
                for y in range(8):
                    for x in range(8):
                        difference = (difference << 1) | (thumb[y*9+x] > thumb[y*9+x+1])
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unreadable dataset image: {path}") from exc
    return dimensions, digest.hexdigest(), pixels, difference


def audit(data, groups=None, distance=4, max_comparisons=100000, max_pairs=1000):
    from .datasets import read_yolo
    if type(distance) is not int or not 0 <= distance <= 8 or max_comparisons < 1 or max_pairs < 1:
        raise ValueError("Invalid audit limits")
    data = Path(data).resolve()
    groups = groups or {}
    if not isinstance(groups, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in groups.items()):
        raise ValueError("Groups must map dataset-relative image paths to recording/group IDs")
    normalized_groups = {(data.parent / key).resolve(): value for key, value in groups.items()}
    names, splits = read_yolo(data)
    records, exact, group_splits, stats = [], {}, {}, {}
    candidates, near, checks = {}, [], 0
    clipped = False
    blocks = distance + 1
    for split, samples in splits.items():
        counts = dict(images=0, boxes=0, backgrounds=0, class_boxes={name: 0 for name in names})
        stats[split] = counts
        for sample in samples:
            dimensions, byte_hash, pixel_hash, dhash = fingerprints(sample.image)
            path = str(sample.image.resolve())
            record = dict(index=len(records), split=split, path=path, sha256=byte_hash,
                          pixel_sha256=pixel_hash, image_size_px=list(dimensions))
            group = normalized_groups.get(sample.image.resolve())
            if group:
                record["group"] = group
                group_splits.setdefault(group, set()).add(split)
            counts["images"] += 1
            counts["boxes"] += len(sample.boxes)
            counts["backgrounds"] += not sample.boxes
            for cls, *_ in sample.boxes:
                counts["class_boxes"][names[cls]] += 1
            exact.setdefault(pixel_hash, []).append(record["index"])
            # Hamming <= distance implies at least one of distance+1 blocks agrees.
            keys = [(block, (dhash >> (64*block//blocks)) & ((1 << (64*(block+1)//blocks-64*block//blocks))-1))
                    for block in range(blocks)]
            possible = set()
            if not clipped:
                for key in keys:
                    possible.update(candidates.get(key, []))
            for previous in sorted(possible):
                if checks >= max_comparisons or len(near) >= max_pairs:
                    clipped = True
                    break
                checks += 1
                old = records[previous]
                if old["pixel_sha256"] == pixel_hash:
                    continue
                diff = (old["dhash"] ^ dhash).bit_count()
                if diff <= distance:
                    near.append(dict(first=previous, second=record["index"], distance=diff,
                                     cross_split=old["split"] != split))
            record["dhash"] = dhash
            records.append(record)
            if not clipped:
                for key in keys:
                    candidates.setdefault(key, []).append(record["index"])
    used = {Path(row["path"]) for row in records}
    unknown = set(normalized_groups) - used
    if unknown:
        raise ValueError("Group manifest contains images absent from the dataset")
    duplicates = [dict(indices=indices, cross_split=len({records[i]["split"] for i in indices}) > 1)
                  for indices in exact.values() if len(indices) > 1]
    return dict(schema_version=1, classes=names, splits=stats, samples=records,
                exact_pixel_duplicates=duplicates, near_duplicate_candidates=near,
                near_duplicate_policy="review_only_dhash", comparisons=checks, search_truncated=clipped,
                group_leakage={g: sorted(s) for g, s in group_splits.items() if len(s) > 1},
                missing_classes={s: [n for n, count in row["class_boxes"].items() if not count] for s, row in stats.items()})
