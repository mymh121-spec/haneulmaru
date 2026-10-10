"""Restore separated training assets from the original offline ZIP."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

ASSETS = {"images", "labels", "marker_labels", "annotations", "preview.jpg",
          "preview_samples.json", "미리보기.html"}


def restore(archive: Path) -> int:
    base = Path(__file__).resolve().parent
    if not archive.is_file():
        raise ValueError("wan_V1_original.zip을 wan_V1 폴더 옆에 두거나 --archive로 지정하세요")
    occupied = [name for name in ASSETS if (base / name).exists()]
    if occupied:
        raise ValueError("기존 학습 자료를 덮어쓰지 않습니다: " + ", ".join(sorted(occupied)))
    prefix = "wan_V1/photo_color_v1/"
    with zipfile.ZipFile(archive) as zipped, tempfile.TemporaryDirectory(prefix=".restore-", dir=base) as temporary:
        stage = Path(temporary)
        entries = []
        seen = set()
        for entry in zipped.infolist():
            if entry.is_dir() or not entry.filename.startswith(prefix):
                continue
            relative = PurePosixPath(entry.filename[len(prefix):])
            if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                    or "\\" in str(relative) or ":" in str(relative)):
                raise ValueError("잘못된 ZIP 경로")
            if relative.parts[0] not in ASSETS:
                continue
            if relative in seen or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("중복 경로 또는 지원하지 않는 ZIP 항목")
            seen.add(relative)
            entries.append((entry, relative))
        if {path.parts[0] for _, path in entries} != ASSETS:
            raise ValueError("원본 ZIP에 학습 자료가 모두 들어 있지 않습니다")
        for entry, relative in entries:
            target = stage.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=65536)
        moved = []
        try:
            for name in sorted(ASSETS):
                (stage / name).rename(base / name)
                moved.append(name)
        except BaseException:
            for name in reversed(moved):
                (base / name).rename(stage / name)
            raise
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="분리한 사진·라벨 학습 자료 복원")
    parser.add_argument("--archive", type=Path,
                        default=Path(__file__).resolve().parents[2] / "wan_V1_original.zip")
    args = parser.parse_args()
    try:
        count = restore(args.archive)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"복원 중단: {error}\n")
    print(f"학습 자료 {count}개 복원 완료. prepare_yolo_rgb.py로 R/B/G 데이터를 변환하세요.")
