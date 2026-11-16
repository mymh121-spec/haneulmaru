"""Finite restart budgets. Every restarted pipeline gets a new ID session."""
from dataclasses import replace
import time

from .contracts import SetupError


class Budget:
    def __init__(self, maximum=0, backoff_s=.1):
        if type(maximum) is not int or not 0 <= maximum <= 20 or not 0 <= backoff_s <= 10:
            raise ValueError("Recovery budget: 0..20 restarts, 0..10 seconds backoff")
        self.maximum, self.backoff_s, self.used = maximum, backoff_s, 0

    def retry(self):
        if self.used >= self.maximum:
            return False
        self.used += 1
        time.sleep(min(self.backoff_s * 2**(self.used - 1), 5))
        return True


class RecoveringPipeline:
    def __init__(self, factory, budget):
        self.factory, self.budget = factory, budget
        self.pipeline = None

    def reset(self):
        if self.pipeline is not None:
            old = self.pipeline
            self.pipeline = None
            old.close()

    def process(self, *args, **kwargs):
        while True:
            try:
                if self.pipeline is None:
                    self.pipeline = self.factory()
                packet = self.pipeline.process(*args, **kwargs)
                packet["recovery_count"] = self.budget.used
                return packet
            except SetupError:
                self.reset()
                if not self.budget.retry():
                    raise

    close = reset


class RecoveringSource:
    def __init__(self, factory, budget, *, live=False):
        self.factory, self.budget, self.live = factory, budget, live
        self.source = None
        self.sequence = self.generation = 0

    def read(self):
        while True:
            try:
                if self.source is None:
                    self.source = self.factory()
                frame = self.source.read()
                if frame is None and self.live:
                    raise SetupError("Live input disconnected")
                if frame is not None:
                    self.sequence += 1
                    return replace(frame, sequence=self.sequence)
                return None
            except SetupError:
                self.close()
                if not self.live or not self.budget.retry():
                    raise
                self.generation += 1

    def close(self):
        if self.source is not None:
            old = self.source
            self.source = None
            old.close()
