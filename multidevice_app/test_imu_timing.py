import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

from .rust_host import WifiImu


def sample(elapsed):
    return dict(event="sample", received_elapsed_ms=elapsed, session="phone", source="USB/Wi-Fi",
                direction="up", rate_dps=180, similarity=.99, sample=dict(t_ms=12))


class ImuTimingTests(unittest.TestCase):
    def test_pipe_delay_does_not_rejuvenate_old_samples(self):
        hub = WifiImu()
        hub.started_at = 100.
        with patch("multidevice_app.rust_host.time.monotonic", return_value=101.):
            hub._accept_event(sample(500))
            self.assertFalse(hub.snapshot()["classification"]["valid"])
        with patch("multidevice_app.rust_host.time.monotonic", return_value=101.1):
            hub._accept_event(sample(1000))
            self.assertAlmostEqual(hub.snapshot()["age_ms"], 100)
            hub._accept_event(sample(700))
            self.assertAlmostEqual(hub.snapshot()["age_ms"], 100)
        with patch("multidevice_app.rust_host.time.monotonic", return_value=101.351):
            self.assertIsNone(hub.snapshot()["sample"])

    def test_missing_invalid_and_future_timestamps_are_not_fresh(self):
        hub = WifiImu()
        hub.started_at = 10.
        with patch("multidevice_app.rust_host.time.monotonic", return_value=11.):
            for elapsed in (None, True, -1, float("nan"), float("inf"), "1", 2000):
                hub._accept_event(sample(elapsed))
                self.assertIsNone(hub.latest)

    def test_real_udp_event_stays_stale_after_delayed_pipe_consumption(self):
        gate, received = threading.Event(), threading.Event()

        class DelayedImu(WifiImu):
            def _read(self):
                gate.wait(5)
                super()._read()

            def _accept_event(self, event):
                super()._accept_event(event)
                received.set()

        hub = DelayedImu(bind="127.0.0.1:0")
        self.addCleanup(hub.stop)
        self.addCleanup(gate.set)
        with patch.dict(os.environ, HANEULMARU_PAIR_KEY="test-timing-key"):
            hub.start()
        address, port = hub.address.rsplit(":", 1)
        packet = dict(key="test-timing-key", session="phone", seq=1,
                      packet=dict(v=1, t_ms=12, ax=0, ay=0, az=1, gx=-180, gy=-12, gz=-11))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(json.dumps(packet).encode(), (address, int(port)))
            # Deliberately leave the Python read loop paused past the freshness window.
            time.sleep(.6)
            gate.set()
            self.assertTrue(received.wait(5), "Rust did not produce a sample event")
            self.assertIsNotNone(hub.latest)
            self.assertIn("received_elapsed_ms", hub.latest[1])
            self.assertFalse(hub.snapshot()["classification"]["valid"])
            received.clear()
            packet["seq"] = 2
            sender.sendto(json.dumps(packet).encode(), (address, int(port)))
            self.assertTrue(received.wait(5))
            fresh = hub.snapshot()["classification"]
            self.assertTrue(fresh["valid"])
            self.assertEqual(fresh["direction"], "up")
