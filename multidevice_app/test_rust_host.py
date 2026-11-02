"""Run with python -m unittest multidevice_app.test_rust_host after a Rust release build."""
import struct
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
from .contracts import Frame
from .rust_host import RustEngine, WifiImu


def varint(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number, value):
    if isinstance(value, int):
        return varint(number * 8) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint(number * 8 + 2) + varint(len(value)) + value


def io(name, shape):
    dims = b"".join(field(1, field(1, d)) for d in shape)
    return field(1, name) + field(2, field(1, field(1, 1) + field(2, dims)))


def attr(name, values):
    return field(1, name) + field(20, 7) + b"".join(field(8, x) for x in values)


def node(op, inputs, output, attrs):
    return (b"".join(field(1, x) for x in inputs) + field(2, output) + field(4, op)
            + b"".join(field(5, a) for a in attrs))


def integer(name, value):
    return field(1, name) + field(20, 2) + field(3, value)


def rgb_test_graph():
    # A real ONNX graph whose three scores equal the frame's mean RGB values.
    # Explicit test-only weights keep this independent from trained drone assets.
    nodes = [
        node("ReduceMean", ["images"], "scores", [attr("axes", [2, 3]), integer("keepdims", 0)]),
        node("Gather", ["scores", "class_order"], "ordered", [integer("axis", 1)]),
        node("Unsqueeze", ["ordered"], "classes", [attr("axes", [2])]),
        node("Concat", ["boxes", "classes"], "output0", [integer("axis", 1)]),
    ]
    boxes = (b"".join(field(1, d) for d in [1, 4, 1]) + field(2, 1) + field(8, "boxes")
             + field(9, struct.pack("<4f", 16, 16, 16, 16)))
    order = field(1, 3) + field(2, 7) + field(8, "class_order") + field(9, struct.pack("<3q", 0, 2, 1))
    graph = (b"".join(field(1, n) for n in nodes) + field(2, "rgb_fixture") + field(5, boxes) + field(5, order)
             + field(11, io("images", [1, 3, 32, 32])) + field(12, io("output0", [1, 7, 1])))
    return field(1, 7) + field(7, graph) + field(8, field(2, 11))


class RustHostTests(unittest.TestCase):
    def test_real_graph_receives_bgr_and_returns_rgb_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.onnx").write_bytes(rgb_test_graph())
            cfg = dict(model_path="fixture.onnx", input_size=32, confidence=0.1, nms_iou=0.7,
                       input_name="images", output_name="output0", output_layout="yolo11_raw_1_7_n")
            engine = RustEngine(cfg, root)
            try:
                for channel, expected in [(2, "R"), (0, "B"), (1, "G")]:
                    frame = np.zeros((32, 32, 3), dtype=np.uint8)
                    frame[:, :, channel] = 255
                    results = engine.detect(Frame(1, 0, frame))
                    self.assertEqual([(d.code, d.xyxy) for d in results], [(expected, (8, 8, 24, 24))])
                frame.fill(0)
                self.assertEqual(engine.detect(Frame(2, 50, frame)), [])
            finally:
                engine.close()

    def test_stale_samples_expire(self):
        hub = WifiImu()
        hub.latest = time.monotonic() - 0.5, {"session": "s"}
        self.assertIsNone(hub.snapshot()["sample"])
        self.assertFalse(hub.snapshot()["classification"]["valid"])
