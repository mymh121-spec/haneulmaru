"""Read a known marker region's pixels and return R, B, G or UNKNOWN.

This is a deterministic colour rule, not a trained detector. The manifest
provides only the image path and marker coordinates, never the answer colour.
"""
from __future__ import annotations

import argparse
import colorsys
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

from PIL import Image


def classify_marker(image: Image.Image, bbox) -> str:
    """bbox = (x, y, width, height) in pixels; return a plain letter.

    Samples the central disk to exclude the synthetic tag's black/white rim.
    No file name, colour label, or expected answer is used in this function.
    UNKNOWN means insufficient saturation, brightness, or a mixed colour.
    """
    if len(bbox) != 4:
        raise ValueError("bbox must contain x, y, width, height")
    x, y, w, h = map(float, bbox)
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError("bbox must be finite")
    if w < 4 or h < 4 or x < 0 or y < 0 or x + w > image.width or y + h > image.height:
        raise ValueError("bbox must be inside the image and at least 4x4 pixels")
    rgb = image.convert("RGB")
    cx, cy, radius = x + w / 2, y + h / 2, min(w, h) * 0.30
    votes = Counter({"R": 0, "B": 0, "G": 0})
    samples = 0
    for py in range(math.floor(y), math.ceil(y + h)):
        for px in range(math.floor(x), math.ceil(x + w)):
            if (px + 0.5 - cx) ** 2 + (py + 0.5 - cy) ** 2 > radius ** 2:
                continue
            samples += 1
            hue, saturation, value = colorsys.rgb_to_hsv(*(c / 255 for c in rgb.getpixel((px, py))))
            if saturation < 0.40 or value < 0.20:
                continue
            hue *= 360
            if hue <= 25 or hue >= 340:
                votes["R"] += 1
            elif 75 <= hue <= 165:
                votes["G"] += 1
            elif 190 <= hue <= 255:
                votes["B"] += 1
    ranked = votes.most_common()
    code, count = ranked[0]
    if samples < 3 or count / samples < 0.65 or (count - ranked[1][1]) / samples < 0.30:
        return "UNKNOWN"
    return code


def classify_image(path, bbox) -> str:
    """Convenient function for other Python programs."""
    with Image.open(path) as image:
        return classify_marker(image, bbox)


def dataset_images(root, split="all"):
    """Exclude originals by absent marker coordinates, not filename suffixes."""
    root = Path(root).resolve()
    rows = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for row in rows:
        bbox = row.get("marker_bbox")
        if bbox is None or (split != "all" and row["split"] != split):
            continue
        path = (root / row["image"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Image path is outside the dataset")
        yield path, bbox


def main():
    parser = argparse.ArgumentParser(description="색상 표식 픽셀 판별: 빨강 R / 파랑 B / 초록 G")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--split", choices=("all", "train", "val", "test"), default="all")
    parser.add_argument("--image", type=Path, help="한 장만 판별; 결과 문자만 출력")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("X", "Y", "W", "H"), help="새 사진의 표식 위치")
    parser.add_argument("--output", type=Path, help="일괄 결과를 CSV로 저장 (기본: 터미널)")
    args = parser.parse_args()
    if args.bbox is not None and args.image is None:
        parser.error("--bbox requires --image")
    if args.image is not None and args.output is not None:
        parser.error("--output is for batch mode; omit it for a single image")
    try:
        if args.image is not None:
            bbox = args.bbox
            if bbox is None:
                target = args.image.resolve()
                bbox = next((box for path, box in dataset_images(args.dataset) if path == target), None)
                if bbox is None:
                    parser.error("원본(none)은 제외합니다. 새 사진이면 --bbox X Y W H로 표식 위치를 지정하세요.")
            print(classify_image(args.image, bbox))
            return
        results = []
        for path, bbox in dataset_images(args.dataset, args.split):
            results.append((path.relative_to(args.dataset.resolve()).as_posix(), classify_image(path, bbox)))
        if not results:
            parser.error("처리할 표식 사진이 없습니다.")
        if args.output:
            # Never overwrite an image, source file, or an earlier result.
            with args.output.open("x", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(("image", "code"))
                writer.writerows(results)
            print(f"{len(results)}장 저장: {args.output}", file=sys.stderr)
        else:
            writer = csv.writer(sys.stdout)
            writer.writerow(("image", "code"))
            writer.writerows(results)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"오류: {exc}\n")


if __name__ == "__main__":
    main()
