"""Compare the migrated Rust execution against the pre-migration contracts."""
from dataclasses import replace
import json
import random
import unittest

from .contracts import SetupError
from .evaluation import evaluate, match
from .test_support.evaluation_reference import evaluate as reference_evaluate, match as reference_match
from .imu import DirectionClassifier
from .inputs import OpenCVSource, NoImu
from .pipeline import Pipeline
from .recovery import Budget, RecoveringPipeline
from .rust_host import ROOT, RustEngine
from .rust_pipeline import RustPipeline
from .tracking import RustTracking


class RustMigrationTests(unittest.TestCase):
    def assert_equivalent(self, a, b):
        if isinstance(a, dict):
            self.assertEqual(a.keys(), b.keys())
            for k in a:
                self.assert_equivalent(a[k], b[k])
        elif isinstance(a, (list, tuple)):
            self.assertEqual(len(a), len(b))
            for x, y in zip(a, b):
                self.assert_equivalent(x, y)
        elif type(a) is float:
            self.assertAlmostEqual(a, b, delta=1e-6)
        else:
            self.assertEqual(a, b)

    def config(self):
        return json.loads((ROOT/"demo/config.json").read_text(encoding="utf-8"))["engine"]

    def classifier(self):
        return DirectionClassifier(json.loads((ROOT/"assets/nu40-imu-direction-training.json").read_text(encoding="utf-8")))

    def test_all_demo_frames_match_split_pipeline_with_aliases_and_policies(self):
        for policy in ("allow", "strict"):
            config = dict(self.config(), class_change_policy=policy, display_names={"R":"빨강"})
            classic = Pipeline(RustEngine(config, ROOT/"demo"), RustTracking(class_change_policy=policy), self.classifier(), .9)
            fused = RustPipeline(config, ROOT/"demo", self.classifier(), .9)
            source = OpenCVSource(ROOT/"demo/multi-tracking.avi", recording=True)
            try:
                count = 0
                while (frame := source.read()) is not None:
                    old = classic.process(frame, NoImu().snapshot(), False)
                    new = fused.process(frame, NoImu().snapshot(), False)
                    self.assertEqual(new["pipeline_backend"], "rust")
                    self.assertEqual(json.loads(json.dumps(old["tracks"])), new["tracks"])
                    for key in ("frame_sequence", "frame_timestamp_ms", "image_size_px", "imu", "range_m"):
                        self.assertEqual(old[key], new[key])
                    count += 1
                self.assertEqual(count, 48)
            finally:
                source.close(); classic.close(); fused.close()

    def test_session_resets_and_recovery_after_actual_worker_exit(self):
        source = OpenCVSource(ROOT/"demo/multi-tracking.avi", recording=True)
        frame = source.read(); source.close()
        factory = lambda: RustPipeline(self.config(), ROOT/"demo", self.classifier())
        runner = RecoveringPipeline(factory, Budget(1, 0))
        state = NoImu().snapshot()
        try:
            previous = None
            for seq, stamp, changed in ((1,0,False),(2,10,False),(3,1011,True),(4,0,True)):
                result = runner.process(replace(frame, sequence=seq, timestamp_ms=stamp), state, False)
                if previous is not None:
                    self.assertEqual(previous != result["session_id"], changed)
                previous = result["session_id"]
            resized = replace(frame, sequence=5, timestamp_ms=1, image=frame.image[:-1])
            result = runner.process(resized, state, False)
            self.assertNotEqual(previous, result["session_id"])
            previous = result["session_id"]
            runner.pipeline.worker.proc.kill()
            runner.pipeline.worker.proc.wait(timeout=2)
            recovered = runner.process(replace(resized, sequence=6, timestamp_ms=2), state, False)
            self.assertNotEqual(previous, recovered["session_id"])
            self.assertEqual(recovered["recovery_count"], 1)
            with self.assertRaises(SetupError):  # duplicate sequence, budget already exhausted
                runner.process(replace(resized, sequence=6, timestamp_ms=2), state, False)
        finally:
            runner.close()

    def test_randomized_full_evaluation_matches_frozen_python_reference(self):
        rng = random.Random(1602)
        refs, preds = [], []
        for repeat in range(2):
            for i in range(30):
                actual, predicted = [], []
                for identity in range(rng.randrange(12)):
                    x,y,w,h = [rng.uniform(2,30) for _ in range(4)]
                    code = rng.choice(["R","B","G"])
                    track = dict(id=identity, code=code, xyxy=[x,y,x+w,y+h])
                    actual.append(track)
                    if rng.random()>.2:
                        shift=rng.uniform(-2,2)
                        predicted.append(dict(track, id=None if rng.random()<.1 else str(identity),
                            code=code if rng.random()>.15 else "UNKNOWN", xyxy=[x+shift,y,x+w+shift,y+h]))
                base=dict(frame_sequence=i,replay_pass=repeat,frame_timestamp_ms=i*50,image_size_px=[100,100])
                refs.append(dict(base,tracks=actual))
                if i%13:
                    preds.append(dict(base,tracks=predicted,session_id="a" if i<15 else "b",
                        engine=dict(inference_ms=rng.random()*10, model_verification=dict(model_sha256="a"*64)),
                        processing_ms=rng.random()*20))
        preds.append(dict(frame_sequence=99,tracks=[],replay_pass=0))
        for threshold in (.3,.5,.9):
            self.assert_equivalent(reference_evaluate(refs,preds,threshold,warmup=3),evaluate(refs,preds,threshold,warmup=3))

    def test_matching_ties_precision_and_empty_sides_match_reference(self):
        hit=lambda x:dict(code="R",id=None,xyxy=[x,0,x+10,10])
        for a,b in (([],[hit(0)]),([hit(0)],[]),([hit(0),hit(0)],[hit(0),hit(0)]),
                    ([hit(0)],[hit(3.333333333333333)])):
            for threshold in (.5,.5000000000000001):
                self.assert_equivalent(reference_match(a,b,threshold),match(a,b,threshold))
