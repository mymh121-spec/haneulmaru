"""Train YOLO11 coloured-drone detection; run this on the training PC."""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="YOLO R/B/G 드론 탐지 학습")
    parser.add_argument("--data", type=Path, default=base / "yolo_rgb" / "data.yaml")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--device", default="0", help="NVIDIA GPU: 0, CPU: cpu")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--project", type=Path, default=base / "runs_rgb")
    parser.add_argument("--resume", type=Path, help="중단된 학습의 weights/last.pt")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.imgsz < 32 or args.workers < 0:
        parser.error("epochs/batch/imgsz must be positive, workers must be nonnegative")
    if args.resume and not args.resume.is_file():
        parser.error("Resume checkpoint does not exist")
    if not args.resume and not args.data.is_file():
        parser.error("먼저 python prepare_yolo_rgb.py 를 실행하세요.")
    import torch
    from ultralytics import YOLO
    if args.device != "cpu" and not torch.cuda.is_available():
        parser.error("CUDA GPU를 사용할 수 없습니다. CUDA 지원 PyTorch를 설치하거나 --device cpu를 지정하세요.")
    if args.resume:
        model = YOLO(str(args.resume.resolve()))
        model.train(resume=True, device=args.device)
    else:
        model = YOLO(args.model)
        model.train(
            data=str(args.data.resolve()), epochs=args.epochs, batch=args.batch,
            imgsz=args.imgsz, workers=args.workers, device=args.device,
            project=str(args.project.resolve()), name="drone_rgb", exist_ok=False,
            seed=20261010, deterministic=True, patience=20, cache=False,
            # Colour is the label: never swap channels or change hue/saturation.
            hsv_h=0.0, hsv_s=0.0, hsv_v=0.15, bgr=0.0,
            # Preserve small tags; avoid mixed or severely cropped colour cues.
            mosaic=0.0, mixup=0.0, cutmix=0.0, copy_paste=0.0,
            translate=0.0, scale=0.0, degrees=0.0, fliplr=0.5,
            val=True, plots=True, save=True,
        )
    print("학습된 모델:", model.trainer.best)
    print("이어 학습할 모델:", model.trainer.last)


if __name__ == "__main__":
    main()
