"""Finite pipe reads/writes and owned process-group shutdown; no provider policy."""

import os
import queue
import signal
import subprocess
import threading
from typing import IO

PIPE_CHARS = 4096
TERMINATE_GRACE_SECONDS = 5.0
PIPE_FAILURE = object()


def _enqueue(messages, value, stopped):
    while not stopped.is_set():
        try:
            messages.put(value, timeout=0.05)
            return
        except queue.Full:
            continue


def pump_lines(stream_name, stream: IO[str] | None, messages, stopped: threading.Event):
    try:
        if stream is not None:
            for chunk in iter(lambda: stream.readline(PIPE_CHARS), ""):
                if stopped.is_set():
                    break
                _enqueue(messages, (stream_name, chunk), stopped)
    except (OSError, ValueError):
        _enqueue(messages, (stream_name, PIPE_FAILURE), stopped)
    finally:
        try:
            if stream is not None:
                stream.close()
        except (OSError, ValueError):
            _enqueue(messages, (stream_name, PIPE_FAILURE), stopped)
        _enqueue(messages, (stream_name, None), stopped)


def write_input(stream: IO[str], value: str):
    try:
        stream.write(value)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass  # A child is allowed to close its input before consuming it all.
    finally:
        try:
            stream.close()
        except (BrokenPipeError, OSError):
            pass


def terminate_process_group(process: subprocess.Popen[str]):
    if os.name == "posix":
        try:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # The leader may already have exited, or may exit on TERM while
            # descendants ignore it. Neither case releases group ownership.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    elif process.poll() is None:  # pragma: no cover - Windows compatibility
        process.terminate()
        try:
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
