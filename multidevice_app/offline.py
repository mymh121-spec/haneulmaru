"""Hardware-free evaluation, dataset audits, deployment and review reports."""
import argparse
import json
from pathlib import Path

from .contracts import SetupError


def write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path:
        with Path(path).open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")


def main():
    parser = argparse.ArgumentParser(description="하드웨어 없는 평가·데이터 검사·배포·결과 검토")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluation = commands.add_parser("evaluate", help="같은 정답에 여러 결과 파일 비교")
    evaluation.add_argument("--reference", type=Path, required=True)
    evaluation.add_argument("--predictions", type=Path, nargs="+", required=True)
    evaluation.add_argument("--iou", type=float, default=.5)
    evaluation.add_argument("--warmup", type=int, default=0)
    evaluation.add_argument("--output", type=Path)
    audit = commands.add_parser("audit", help="클래스 분포, 픽셀 중복, 유사 이미지, 원본 그룹 유출")
    audit.add_argument("--data", type=Path, required=True)
    audit.add_argument("--groups", type=Path)
    audit.add_argument("--distance", type=int, default=4)
    audit.add_argument("--max-comparisons", type=int, default=100000)
    audit.add_argument("--output", type=Path)
    bundle = commands.add_parser("bundle", help="가중치·프로필·클래스·검증 기록 배포 폴더 생성")
    bundle.add_argument("--profile", type=Path, required=True)
    bundle.add_argument("--training-classes", type=Path, required=True)
    bundle.add_argument("--dataset-report", type=Path)
    bundle.add_argument("--evaluation", type=Path)
    bundle.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-bundle")
    verify.add_argument("path", type=Path)
    report = commands.add_parser("report", help="로컬 브라우저에서 열 수 있는 결과 검토 HTML")
    report.add_argument("--input", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            from .evaluation import evaluate_files
            reports = [dict(prediction=path.name, **evaluate_files(args.reference, path,
                       threshold=args.iou, warmup=args.warmup)) for path in args.predictions]
            write_json(args.output, reports[0] if len(reports) == 1 else dict(comparisons=reports))
        elif args.command == "audit":
            from .dataset_quality import audit
            groups = json.loads(args.groups.read_text(encoding="utf-8-sig")) if args.groups else None
            write_json(args.output, audit(args.data, groups, args.distance, args.max_comparisons))
        elif args.command == "bundle":
            from .deployment import bundle
            result = bundle(args.profile, args.training_classes, args.output, args.dataset_report, args.evaluation)
            write_json(None, dict(output=str(args.output), **result))
        elif args.command == "verify-bundle":
            from .deployment import verify_bundle
            write_json(None, verify_bundle(args.path))
        else:
            from .review_report import write_report
            write_report(args.input, args.output)
            print(args.output)
    except (SetupError, OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"오프라인 작업 실패: {exc}\n")


if __name__ == "__main__":
    main()
