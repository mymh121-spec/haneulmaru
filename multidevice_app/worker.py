"""Bounded, synchronous pipe exchanges with one child process (Windows/Linux)."""
import math
import io
import subprocess
import threading
import time

from .contracts import SetupError


def timeout_seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise SetupError("Worker timeout must be a positive finite number of seconds")
    return value


class PipeWorker:
    def __init__(self, args, *, env=None, max_response_bytes=1024*1024):
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("Response limit must be a positive integer")
        # Unbuffered pipes let the I/O thread own all writes, including flushes.
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, env=env)
        # Buffer reads only: raw FileIO.readline() performs a syscall per byte.
        # Writes stay unbuffered, and readers are closed only after the I/O thread joins.
        self.reader = io.BufferedReader(self.proc.stdout, buffer_size=65536)
        self.max_response_bytes = max_response_bytes
        self.thread = None
        self.closed = False

    def exchange(self, payload, timeout, stage, *, response_reader=None):
        timeout = timeout_seconds(timeout)
        if self.closed:
            raise SetupError(f"{stage}: worker is closed; create a new worker")
        done, result = threading.Event(), []
        phase = ["send"]

        def communicate():
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = self.proc.stdin.write(remaining)
                    if not written:
                        raise OSError("closed input pipe")
                    remaining = remaining[written:]
                phase[0] = "response"
                result.append((response_reader or self.readline)())
            except Exception as exc:
                result.append(exc)
            finally:
                done.set()

        # Reserve part of the total deadline for kill/reap/join, rather than
        # adding an unbounded wait after a timed-out pipe operation.
        deadline = time.monotonic() + timeout
        reserve = min(0.5, timeout / 4)
        self.thread = threading.Thread(target=communicate, daemon=True, name="haneulmaru-worker-io")
        self.thread.start()
        try:
            completed = done.wait(max(0, deadline - time.monotonic() - reserve))
        except BaseException:
            self.close()
            raise
        if not completed:
            failed_phase = phase[0]
            self.close(deadline=deadline)
            raise SetupError(f"{stage}: {failed_phase} timeout ({timeout:g}s total budget)")
        if isinstance(result[0], Exception):
            failed_phase = phase[0]
            self.close(deadline=deadline)
            raise SetupError(f"{stage}: {failed_phase} failed; worker disconnected") from result[0]
        return result[0]

    def readline(self):
        """Every consumer must use this buffer, including post-handshake streams."""
        line = self.reader.readline(self.max_response_bytes + 1)
        if not line:
            raise OSError("closed output pipe")
        if len(line) > self.max_response_bytes or not line.endswith(b"\n"):
            raise OSError("invalid or oversized response line")
        return line

    def start_stream(self, consumer):
        """Transfer reader ownership after the final exchange; close joins it."""
        if self.closed:
            raise SetupError("Cannot stream from a closed worker")
        if self.thread:
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                raise SetupError("Worker exchange is still active")
        self.thread = threading.Thread(target=consumer, daemon=True, name="haneulmaru-worker-stream")
        self.thread.start()
        return self.thread

    def close(self, *, deadline=None):
        if self.closed and self.proc.stdin.closed and self.proc.stdout.closed:
            return
        self.closed = True
        deadline = time.monotonic() + 1 if deadline is None else deadline
        # Kill before closing pipes: closing a pipe while a write is blocked can
        # itself block on buffered I/O locks. A failed protocol cannot be reused.
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        try:
            self.proc.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise SetupError("Worker cleanup timeout after kill") from exc
        if self.thread:
            self.thread.join(timeout=max(0, deadline - time.monotonic()))
            if self.thread.is_alive():
                raise SetupError("Worker pipe cleanup timeout after kill")
        self.proc.stdin.close()
        self.reader.close()
