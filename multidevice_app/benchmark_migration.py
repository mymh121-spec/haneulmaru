"""Reproducible CPU microbenchmarks; not a device/real-model performance claim."""
import json
import statistics
import time

from .evaluation import evaluate
from .test_support.evaluation_reference import evaluate as python_evaluate
from .imu import DirectionClassifier
from .inputs import OpenCVSource, NoImu
from .pipeline import Pipeline
from .rust_host import ROOT, RustEngine
from .rust_pipeline import RustPipeline
from .tracking import RustTracking


def main():
    config = json.loads((ROOT/"demo/config.json").read_text(encoding="utf-8"))["engine"]
    model = json.loads((ROOT/"assets/nu40-imu-direction-training.json").read_text(encoding="utf-8"))
    source = OpenCVSource(ROOT/"demo/multi-tracking.avi", recording=True)
    frames = []
    try:
        while (frame := source.read()) is not None:
            frames.append(frame)
    finally:
        source.close()
    factories = {
        "split": lambda: Pipeline(RustEngine(config, ROOT/"demo"), RustTracking(), DirectionClassifier(model)),
        "combined": lambda: RustPipeline(config, ROOT/"demo", DirectionClassifier(model)),
    }
    results = {}
    for name, factory in factories.items():
        samples = []
        for _ in range(3):
            runner = factory()
            try:
                started = time.perf_counter()
                packets = [runner.process(f, NoImu().snapshot(), False) for f in frames]
                samples.append((time.perf_counter()-started)*1000)
                assert sum(len(p["tracks"]) for p in packets) == 177
            finally:
                runner.close()
        results[name] = dict(median_ms=statistics.median(samples), runs_ms=samples)
    # Deliberately dense tied assignment: stresses the cubic matching loop.
    reference = [dict(frame=i, tracks=[dict(id=k, code="R", xyxy=[0,0,10,10]) for k in range(100)]) for i in range(10)]
    evaluation = {}
    for name, fn in (("python_reference", python_evaluate), ("rust", evaluate)):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            report = fn(reference, reference)
            samples.append((time.perf_counter()-started)*1000)
            assert report["totals"]["tp"] == 1000 and report["totals"]["id_switches"] == 0
        evaluation[name] = dict(median_ms=statistics.median(samples), runs_ms=samples)
    print(json.dumps(dict(pipeline_48_synthetic_frames_excludes_decode_and_model_startup=results,
                          evaluation_10_frames_100_overlapping_objects_includes_rust_startup=evaluation), indent=2))


if __name__ == "__main__":
    main()
