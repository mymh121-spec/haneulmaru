"""Single synchronous frame in flight; no growing frame queue."""
import time
import uuid
import math


class Pipeline:
    def __init__(self, engine, tracker, direction_classifier, color_confidence=0.4, max_gap_ms=1000):
        if not math.isfinite(color_confidence) or not 0 <= color_confidence <= 1 or not math.isfinite(max_gap_ms) or max_gap_ms <= 0:
            raise ValueError("Invalid pipeline thresholds")
        self.engine, self.tracker, self.direction_classifier = engine, tracker, direction_classifier
        self.color_confidence, self.max_gap_ms = color_confidence, max_gap_ms
        self.session = uuid.uuid4().hex
        self.previous = None

    def process(self, frame, imu_state, attach_live_imu=True):
        if (frame.image.ndim != 3 or frame.image.shape[2] != 3 or
                str(frame.image.dtype) != "uint8" or min(frame.image.shape[:2]) <= 0):
            raise ValueError("Frame must be a nonempty upright BGR uint8 image")
        if type(frame.sequence) is not int or frame.sequence < 0 or not math.isfinite(frame.timestamp_ms) or frame.timestamp_ms < 0:
            raise ValueError("Invalid frame identity/time")
        if self.previous and frame.sequence <= self.previous[0]:
            raise ValueError("Frames must be ordered")
        if self.previous and (frame.timestamp_ms < self.previous[1] or
                              frame.timestamp_ms - self.previous[1] > self.max_gap_ms):
            self.tracker.reset()
            self.session = uuid.uuid4().hex
        self.previous = frame.sequence, frame.timestamp_ms
        # Snapshot/classify before inference. Never attach a newer IMU sample
        # merely because inference for this frame finished late.
        imu = (self.direction_classifier.classify(imu_state) if attach_live_imu else
               dict(valid=False, direction=None, reason="recording_has_no_synchronized_imu"))
        start = time.perf_counter()
        detections = self.engine.detect(frame)
        inference_ms = (time.perf_counter() - start) * 1000
        height, width = frame.image.shape[:2]
        for detection in detections:
            detection.validate(width, height)
        tracks = self.tracker.update(frame, detections)
        output = []
        for track in tracks:
            track.validate(width, height)
            entry = track.packet()
            entry["raw_code"] = entry["code"]
            if entry["confidence"] < self.color_confidence:
                entry["code"] = "UNKNOWN"
            output.append(entry)
        return dict(schema_version=1, app="haneulmaru", session_id=self.session,
                    frame_sequence=frame.sequence, frame_timestamp_ms=frame.timestamp_ms,
                    timestamp_basis=frame.timestamp_basis, image_size_px=[width, height],
                    orientation="upright_unmirrored", imu=imu, tracks=output,
                    engine=dict(name=self.engine.name, ready=self.engine.ready, reason=self.engine.reason,
                                executed=self.engine.ready, inference_ms=inference_ms),
                    processing_ms=(time.perf_counter() - start) * 1000,
                    execution_device="host", range_m=None)

    def close(self):
        self.engine.close()
