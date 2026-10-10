"""Read the existing authenticated NU40 server without opening another USB port."""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from drone_coordinates import GyroPose


class ImuPosePoller:
    def __init__(self, host_url, model):
        parsed = urlsplit(host_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.fragment:
            raise ValueError("실행 창에 나온 PC 주소를 #연결키까지 지정하세요")
        self.url = urlunsplit((parsed.scheme, parsed.netloc, "/stream/imu", "", ""))
        self.token = parsed.fragment
        self.pose = GyroPose(model)
        self.lock = threading.Lock()
        self.updated_at = 0.0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def snapshot(self):
        with self.lock:
            if time.monotonic() - self.updated_at > .35:
                self.pose.consume({})
            return self.pose.snapshot()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                request = Request(self.url, headers={"Authorization": "Bearer " + self.token})
                with urlopen(request, timeout=1) as response:
                    state = json.load(response)
                with self.lock:
                    self.pose.consume(state)
                    self.updated_at = time.monotonic()
            except Exception:
                # Drop uncertain intervals, without exposing the connection key.
                with self.lock:
                    self.pose.consume({})
            self.stop_event.wait(0.02)
