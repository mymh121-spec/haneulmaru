"""Thin input/IMU adapter for one Rust detect-track-packet worker."""
import json
import time
import uuid

from .contracts import Detection, SetupError
from .model_profile import detector_options, profile_metadata, verify_model
from .replay import write_cached_detections
from .rust_host import binary
from .worker import PipeWorker, timeout_seconds


class RustPipeline:
    def __init__(self, config, root, classifier, color_confidence=.4, max_gap_ms=1000, cache=None):
        weights = root / config["model_path"]
        if config.get("device", "cpu") != "cpu" or not weights.is_file():
            raise SetupError("Rust pipeline requires a local ONNX model and device=cpu")
        metadata = profile_metadata(config)
        self.verification = verify_model(config, weights)
        self.aliases, self.cache, self.classifier = metadata["display_names"], cache, classifier
        self.timeout = timeout_seconds(config.get("request_timeout_s", 5))
        startup = timeout_seconds(config.get("startup_timeout_s", 30))
        options = dict(detector_options(config), color_confidence=color_confidence, max_gap_ms=max_gap_ms,
                       session_prefix=uuid.uuid4().hex, class_change_policy=metadata["class_change_policy"],
                       display_names=self.aliases, model_verification=self.verification, include_detections=cache is not None)
        self.worker = PipeWorker([binary(), "pipeline", str(weights), str(config["input_size"]),
                                  str(config["confidence"]), str(config["nms_iou"]),
                                  config["input_name"], config["output_name"]])
        try:
            ready = json.loads(self.worker.exchange(json.dumps(options).encode()+b"\n", startup, "Rust pipeline startup"))
            if ready != {"ready": True, "pipeline_protocol": 1}:
                raise SetupError("Rust pipeline protocol mismatch; rebuild the worker")
        except Exception:
            self.close()
            raise

    def process(self, frame, imu_state, attach_live_imu=True):
        if (frame.image.ndim != 3 or frame.image.shape[2] != 3 or str(frame.image.dtype) != "uint8"
                or min(frame.image.shape[:2]) <= 0):
            raise ValueError("Frame must be a nonempty upright BGR uint8 image")
        imu = ((imu_state["classification"] if "classification" in imu_state else self.classifier.classify(imu_state))
               if attach_live_imu else dict(valid=False, direction=None, reason="recording_has_no_synchronized_imu"))
        height, width = frame.image.shape[:2]
        header = dict(sequence=frame.sequence, timestamp_ms=frame.timestamp_ms, width=width, height=height,
                      timestamp_basis=frame.timestamp_basis, imu=imu)
        start = time.perf_counter()
        try:
            payload = json.dumps(header, allow_nan=False).encode()+b"\n"+frame.image.tobytes(order="C")
            packet = json.loads(self.worker.exchange(payload, self.timeout, "Rust pipeline frame"))
            if (packet.get("frame_sequence") != frame.sequence or packet.get("frame_timestamp_ms") != frame.timestamp_ms
                    or packet.get("image_size_px") != [width,height] or packet.get("pipeline_backend") != "rust"
                    or not isinstance(packet.get("session_id"), str) or not packet["session_id"]
                    or not isinstance(packet.get("tracks"), list)):
                raise ValueError("Invalid pipeline response identity")
            ids = set()
            for row in packet["tracks"]:
                Detection(row["code"], row["confidence"], tuple(row["xyxy"])).validate(width,height)
                identity = row["id"]
                if identity is not None:
                    if type(identity) is not int or identity <= 0 or identity in ids:
                        raise ValueError("Invalid pipeline tracking IDs")
                    ids.add(identity)
            raw = packet.pop("_detections", None)
            if self.cache is not None:
                if not isinstance(raw, list):
                    raise ValueError("Missing raw detection cache")
                write_cached_detections(self.cache, frame, raw, self.verification, self.aliases)
            packet["worker_processing_ms"] = packet["processing_ms"]
            packet["processing_ms"] = (time.perf_counter()-start)*1000
            return packet
        except (ValueError, TypeError, KeyError) as exc:
            self.close()
            raise SetupError("Invalid Rust pipeline frame/response") from exc

    def close(self):
        self.worker.close()
