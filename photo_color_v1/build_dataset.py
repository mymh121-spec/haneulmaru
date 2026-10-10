"""Reproducible, offline COCO -> static-photo marker experiment dataset.

Input is a user-downloaded Roboflow COCO ZIP. No API credentials, training,
network calls, camera capture, tracking, or app integration are involved.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import html
import io
import json
import math
import random
import re
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

SOURCE_URL = "https://universe.roboflow.com/ai-bmkoo/drone-detection-inlmy/dataset/1"
COLORS = {"red": (225, 37, 47), "blue": (32, 91, 225), "green": (30, 182, 72)}
SPLITS = ("train", "val", "test")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def difference_hash(image):
    pixels = image.convert("L").resize((9, 8)).tobytes()
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | (pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return value


def normalized_box(box, width, height):
    x, y, w, h = box
    return [(x + w / 2) / width, (y + h / 2) / height, w / width, h / height]


def checked_box(box, width, height):
    x, y, w, h = map(float, box)
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise ValueError(f"Invalid bbox: {box}")
    left, top = max(0, x), max(0, y)
    right, bottom = min(width, x + w), min(height, y + h)
    if right <= left or bottom <= top:
        raise ValueError(f"BBox outside image: {box}")
    return [left, top, right - left, bottom - top]


def read_candidates(archive):
    items, report = [], {"source_splits": {}, "categories": {}, "rejected": collections.Counter()}
    with zipfile.ZipFile(archive) as z:
        for split in ("train", "valid", "test"):
            data = json.loads(z.read(f"{split}/_annotations.coco.json"))
            categories = {c["id"]: c["name"] for c in data["categories"]}
            report["categories"][split] = categories
            report["source_splits"][split] = {"images": len(data["images"]), "annotations": len(data["annotations"])}
            by_image = collections.defaultdict(list)
            for ann in data["annotations"]:
                # This exact source uses category 1 named '0', parent 'Drones'.
                # Refuse different category layouts instead of merging birds/other objects.
                if categories.get(ann["category_id"]) != "0" or ann["category_id"] != 1:
                    raise ValueError("Unexpected source category. Inspect it before remapping to drone.")
                by_image[ann["image_id"]].append(ann)
            if categories != {0: "Drones", 1: "0"}:
                raise ValueError("This adapter is for the documented AI/Drone-Detection v1 export only.")
            for entry in data["images"]:
                anns = by_image[entry["id"]]
                if len(anns) != 1:
                    report["rejected"]["not_exactly_one_object"] += 1
                    continue
                member = f"{split}/{entry['file_name']}"
                raw = z.read(member)
                pic = Image.open(io.BytesIO(raw)).convert("RGB")
                if pic.size != (entry["width"], entry["height"]):
                    raise ValueError(f"Image/annotation size mismatch: {member}")
                box = checked_box(anns[0]["bbox"], *pic.size)
                if min(box[2:]) < 120:
                    report["rejected"]["bbox_short_edge_below_120px"] += 1
                    continue
                items.append({"source_member": member, "source_split": split,
                              "source_name": re.sub(r"\.rf\..*$", "", entry["file_name"]),
                              "source_sha256": sha(raw), "pixel_sha256": sha(pic.tobytes()),
                              "dhash64": difference_hash(pic), "bbox": box,
                              "width": pic.width, "height": pic.height,
                              "source_annotation_id": anns[0]["id"]})
    report["eligible_before_dedup"] = len(items)
    report["rejected"] = dict(report["rejected"])
    return items, report


def select_diverse(items, count, seed):
    """Greedy near-duplicate exclusion BEFORE splits; all variants stay together."""
    random.Random(seed).shuffle(items)
    chosen, known_names, pixel_hashes, hashes, excluded = [], set(), set(), [], 0
    for item in items:
        duplicate = (item["source_name"] in known_names or item["pixel_sha256"] in pixel_hashes
                     or any((item["dhash64"] ^ h).bit_count() <= 4 for h in hashes))
        if duplicate:
            excluded += 1
            continue
        chosen.append(item)
        known_names.add(item["source_name"])
        pixel_hashes.add(item["pixel_sha256"])
        hashes.append(item["dhash64"])
        if len(chosen) == count:
            break
    if len(chosen) < count:
        raise ValueError(f"Only {len(chosen)} sufficiently distinct candidates for requested {count}")
    random.Random(seed + 1).shuffle(chosen)
    return chosen, excluded


def marker_box(item, rng):
    x, y, w, h = item["bbox"]
    size = round(max(14, min(44, min(w, h) * rng.uniform(.13, .19))))
    cx, cy = x + w * rng.uniform(.46, .54), y + h * rng.uniform(.44, .54)
    return [round(cx - size / 2), round(cy - size / 2), size, size]


def add_marker(image, box, color, brightness):
    # Deterministic training-data augmentation: a flat printed circular tag.
    # BBox center is a placement heuristic, NOT a body surface segmentation.
    x, y, w, h = box
    result = image.copy()
    layer = Image.new("RGBA", (w * 4, h * 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rgb = tuple(round(v * brightness) for v in COLORS[color])
    draw.ellipse((0, 0, w * 4 - 1, h * 4 - 1), fill=(245, 245, 245, 255))
    draw.ellipse((4, 4, w * 4 - 5, h * 4 - 5), fill=(24, 24, 24, 255))
    draw.ellipse((8, 8, w * 4 - 9, h * 4 - 9), fill=(*rgb, 255))
    layer = layer.resize((w, h), Image.Resampling.LANCZOS)
    result.paste(layer, (x, y), layer)
    return result


def make_preview(out, rows):
    samples = rows
    write_json(out / "preview_samples.json", samples)
    payload = json.dumps(samples, ensure_ascii=False).replace("<", "\\u003c")
    page = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>하늘마루 · 정지사진 표식 데이터셋</title><style>
body{font:16px system-ui;background:#101a27;color:#edf5ff;margin:24px}h1{font-size:26px}button,select{font:inherit;padding:9px;background:#21364d;color:white;border:1px solid #54708a;border-radius:8px}header{position:sticky;top:0;background:#101a27;padding:12px 0;z-index:2}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}.card{background:#1d2c40;padding:10px;border-radius:12px}.card img{width:100%;cursor:zoom-in}small{display:block;color:#bad1e8;overflow-wrap:anywhere}dialog{background:#101a27;color:white;border:1px solid #698ba8;max-width:94vw}dialog img{max-height:76vh;max-width:90vw;display:block}a{color:#80dcca}
</style><h1>하늘마루 · 정지사진 색상표식 실험</h1>
<p>객체 클래스: <b>drone</b> 하나 / 합성 표식 속성: red · blue · green. none은 추가 표식 없는 원본입니다.</p>
<p>표식은 사진 위 2D 합성입니다. 정답 숨기기 상태로 사진을 크게 띄워 팀원 테스트에 사용하세요. 모델 추론 화면이 아닙니다.</p>
<header><select id="split"><option value="all">전체 분할</option><option>train</option><option>val</option><option>test</option></select>
<select id="color"><option value="all">전체 색상</option><option>none</option><option>red</option><option>blue</option><option>green</option></select>
<button id="answers">정답 보기</button> <button id="random">무작위 사진</button> <button id="prevPage">이전</button><button id="nextPage">다음</button> <span id="pageInfo"></span> <a href="DATASET_CARD.txt">데이터 설명</a></header>
<div class="grid" id="grid"></div><dialog id="modal"><button id="close">닫기</button><button id="next">다음 사진</button><img id="large"><p id="caption"></p></dialog>
<script>const rows=PAYLOAD;let showAnswers=false,shown=[],current=0,page=0;
const $=id=>document.getElementById(id);function open(i){current=i;const r=shown[i];$('large').src=r.image;$('caption').textContent=showAnswers?r.source_id+' / '+r.marker_color:'정답 숨김';if(!$('modal').open)$('modal').showModal()}
function render(){shown=rows.filter(r=>($('split').value==='all'||r.split===$('split').value)&&($('color').value==='all'||r.marker_color===$('color').value));page=Math.max(0,Math.min(page,Math.ceil(shown.length/48)-1));$('pageInfo').textContent=(page+1)+' / '+Math.ceil(shown.length/48)+' · '+shown.length+'장';$('grid').replaceChildren();shown.slice(page*48,(page+1)*48).forEach((r,i)=>{let c=document.createElement('div');c.className='card';let im=document.createElement('img');im.src=r.image;im.loading='lazy';im.alt='실험 사진';im.onclick=()=>open(page*48+i);let t=document.createElement('small');t.textContent=r.split+' · '+r.source_id+(showAnswers?' · '+r.marker_color:'');c.append(im,t);$('grid').append(c)});}
$('split').onchange=$('color').onchange=()=>{page=0;render()};$('prevPage').onclick=()=>{page--;render()};$('nextPage').onclick=()=>{page++;render()};$('answers').onclick=()=>{showAnswers=!showAnswers;$('answers').textContent=showAnswers?'정답 숨기기':'정답 보기';render()};$('random').onclick=()=>open(Math.floor(Math.random()*shown.length));$('close').onclick=()=>$('modal').close();$('next').onclick=()=>open((current+1)%shown.length);document.addEventListener('keydown',e=>{if(e.key==='ArrowRight'&&$('modal').open)open((current+1)%shown.length)});render();</script></html>""".replace("PAYLOAD", payload)
    (out / "미리보기.html").write_text(page, encoding="utf-8")
    groups = list(dict.fromkeys(r["source_id"] for r in samples if r["split"] == "test"))[:5]
    sheet = Image.new("RGB", (1040, len(groups) * 280), "#e7eef5")
    draw = ImageDraw.Draw(sheet)
    for y, group in enumerate(groups):
        for x, color in enumerate(("none", *COLORS)):
            r = next(r for r in samples if r["source_id"] == group and r["marker_color"] == color)
            im = Image.open(out / r["image"]).convert("RGB")
            im.thumbnail((250, 250))
            sheet.paste(im, (x * 260, y * 280))
            draw.text((x * 260 + 5, y * 280 + 254), f"{group} | {color}", fill="black")
    sheet.save(out / "preview.jpg", quality=88)


def build(args):
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output must be empty; choose a new version to preserve earlier data.")
    out.mkdir(parents=True, exist_ok=True)
    items, audit = read_candidates(args.source)
    chosen, excluded = select_diverse(items, args.count, args.seed)
    boundaries = (round(args.count * .7), round(args.count * .9))
    rows = []
    coco = {s: {"images": [], "annotations": [], "categories": [{"id": 1, "name": "drone"}]} for s in SPLITS}
    for s in SPLITS:
        for d in ("images", "labels", "marker_labels"):
            (out / d / s).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.source) as archive:
        for i, item in enumerate(chosen):
            split = "train" if i < boundaries[0] else "val" if i < boundaries[1] else "test"
            source_id = f"photo_{i + 1:04d}"
            raw = archive.read(item["source_member"])
            original = Image.open(io.BytesIO(raw)).convert("RGB")
            rng = random.Random(f"{args.seed}:{item['source_sha256']}")
            tag = marker_box(item, rng)
            brightness = rng.uniform(.78, 1.0)
            for color in ("none", *COLORS):
                name = f"{source_id}_{color}"
                image_path = out / "images" / split / (name + ".jpg")
                if color == "none":
                    image_path.write_bytes(raw)
                else:
                    add_marker(original, tag, color, brightness).save(image_path, quality=88, subsampling=0)
                normalized = normalized_box(item["bbox"], original.width, original.height)
                (out / "labels" / split / (name + ".txt")).write_text("0 " + " ".join(f"{v:.8f}" for v in normalized) + "\n", encoding="utf-8")
                marker_label = ""
                if color != "none":
                    marker_label = str(list(COLORS).index(color)) + " " + " ".join(f"{v:.8f}" for v in normalized_box(tag, original.width, original.height)) + "\n"
                (out / "marker_labels" / split / (name + ".txt")).write_text(marker_label, encoding="utf-8")
                record = {**item, "source_id": source_id, "split": split, "marker_color": color,
                          "image": image_path.relative_to(out).as_posix(), "sha256": sha(image_path.read_bytes()),
                          "drone_label": f"labels/{split}/{name}.txt", "marker_label": f"marker_labels/{split}/{name}.txt",
                          "marker_bbox": None if color == "none" else tag,
                          "marker_placement": None if color == "none" else "synthetic_2d_bbox_center",
                          "marker_brightness": None if color == "none" else brightness}
                rows.append(record)
                iid = len(coco[split]["images"]) + 1
                coco[split]["images"].append({"id": iid, "file_name": record["image"], "width": original.width, "height": original.height, "source_group": source_id, "marker_color": color})
                x, y, w, h = item["bbox"]
                coco[split]["annotations"].append({"id": iid, "image_id": iid, "category_id": 1, "bbox": [x, y, w, h], "area": w*h, "iscrowd": 0, "attributes": {"marker_color": color, "marker_bbox": record["marker_bbox"]}})
    for split, data in coco.items():
        write_json(out / "annotations" / f"{split}.coco.json", data)
    (out / "data.yaml").write_text("# Pass an absolute path override if your trainer resolves this relative to a global datasets directory.\npath: .\ntrain: images/train\nval: images/val\ntest: images/test\nnc: 1\nnames: ['drone']\n", encoding="utf-8")
    write_json(out / "manifest.json", rows)
    audit.update({"source_url": SOURCE_URL, "source_zip_sha256": sha(args.source.read_bytes()), "seed": args.seed,
                  "selected_source_photos": len(chosen), "excluded_similar_candidates_before_selection_complete": excluded,
                  "images": len(rows), "split_counts": dict(collections.Counter(r["split"] for r in rows)),
                  "marker_counts": dict(collections.Counter(r["marker_color"] for r in rows)),
                  "trained_model": False,
                  "dedup_method": "source stem + decoded RGB SHA256 + greedy dHash64 distance <= 4 exclusion before split",
                  "limitation": "Not a complete video/near-duplicate audit. Synthetic tags are not physically attached or surface-segmented."})
    write_json(out / "build_report.json", audit)
    (out / "DATASET_CARD.txt").write_text(f"""하늘마루 · 정지사진 drone + 색상표식 실험 v1

실제 원본 사진 {len(chosen)}장 × [원본, 빨강, 파랑, 초록] = {len(rows)}장.
객체 탐지 클래스는 drone 하나입니다. YOLO id=0 / COCO id=1.
marker_color는 별도 속성입니다. red/blue/green을 drone의 다른 클래스에 섞지 않습니다.
none은 '합성 표식을 추가하지 않은 원본'이며 원래 기체나 배경에 색이 없다는 의미가 아닙니다.

출처: AI / Drone-Detection / v1, Roboflow Universe
{SOURCE_URL}
제공자 명시 라이선스: CC BY 4.0
https://creativecommons.org/licenses/by/4.0/
일부 원본에 상용 사진 워터마크가 보입니다. 개별 사진의 권리까지 확인하지 못했으므로
공개 Git에는 사진을 재배포하지 않습니다. 외부 재배포 전 개별 출처 권리를 확인하세요.
원본 COCO는 category 1의 이름을 '0', 상위 분류를 'Drones'로 표기합니다.
이 버전의 실제 annotation category 1을 drone으로 정규화했습니다.
변경사항: 1개 객체이며 bbox 짧은 변 120px 이상인 사진 선택, 유사 사진 제외,
학습/검증/시험 재분할, 빨강·파랑·초록 원형 표식 합성, COCO/YOLO 변환.
다운로드 원본은 이미 620×620으로 늘려진 이미지입니다. 원본 종횡비 복원은 하지 않았습니다.

사용:
1. 미리보기.html 더블클릭 → test 선택 → 사진 클릭. 정답 숨김으로 발표/정지사진 테스트.
2. labels/는 drone 한 클래스 YOLO bbox, annotations/는 COCO.
3. marker_labels/는 표식 bbox만 담긴 별도 라벨입니다. id 0=red, 1=blue, 2=green.
   marker_labels를 drone 라벨과 합치지 마세요. 데이터로더에서 별도 타깃으로 읽습니다.
4. manifest.json에 사진별 원본 해시·분할·색상·표식 bbox·원본 bbox가 들어 있습니다.
5. 검증 실행: python validate_dataset.py .

검증 원칙:
원본 사진 하나의 네 변형은 같은 split에만 존재합니다. 색상마다 동일 위치·크기·밝기를
사용하여 배경과 특정 색의 상관관계를 줄였습니다. 선택된 원본간 dHash 차이 4 이하는
제외했습니다. 이것만으로 동일 촬영 영상/유사 장면의 모든 누출이 제거되었다고 보장하지 않습니다.

한계:
표식은 사진의 bbox 중심 부근에 합성한 2D 그래픽입니다. 기체 본체 표면에 정확히
부착된 실제 사진이 아니며 일부 표식은 배경/빈 공간에 걸칠 수 있습니다.
원본 라벨은 업로더 제공값이며 전체 수동 재주석을 하지 않았습니다.
이 데이터에는 조명·반사·휴대폰 화면 재촬영·실물 스티커의 실제 변화가 포함되지 않습니다.
모든 사진에 객체가 존재하는 양성 예시 세트입니다. 배경 오탐 성능을 평가하는 음성 세트는 없습니다.
실험 성능을 보고할 때는 따로 촬영한 정지사진 시험 세트를 사용하세요.
색상은 색상만 나타냅니다. 소속·의도·운용 주체를 의미하지 않습니다.
이번 결과는 데이터셋 제작본입니다. 학습 모델/카메라 추론/정확도 측정 결과가 아닙니다.
""", encoding="utf-8")
    for name in ("build_dataset.py", "validate_dataset.py", "requirements.txt"):
        source = Path(__file__).with_name(name)
        if source.exists():
            shutil.copy2(source, out / name)
    make_preview(out, rows)
    from validate_dataset import validate
    result = validate(out)
    write_json(out / "validation_report.json", result)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261010)
    args = parser.parse_args()
    if args.count < 20:
        parser.error("--count must be at least 20 to populate all splits")
    build(args)
