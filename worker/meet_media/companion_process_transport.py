"""Bounded parent-owned JSON pipe, cancellation and process-group lifecycle."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from worker.meet_media.companion_execution_ports import (
    decode,
    encode,
    request_limit,
    validate_request,
)


class CompanionExecutionError(RuntimeError):
    pass


class CompanionProcessTransport:
    def __init__(self, command, *, environment=None, timeout_seconds=120, input_limit=8192, output_limit=2_600_000,
                 ports=None):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
            raise ValueError("meet_companion_execution_timeout_invalid")
        if (type(input_limit) is not int or not 1 <= input_limit <= 2_600_000
                or type(output_limit) is not int or not 1 <= output_limit <= 10_100_000):
            raise ValueError("meet_companion_execution_limits_invalid")
        self._command, self._environment, self._timeout = command, environment, timeout_seconds
        self._input_limit, self._output_limit = input_limit, output_limit
        self._ports = dict(ports or {})
        if any(not isinstance(name, str) or not callable(handler) for name, handler in self._ports.items()):
            raise ValueError("meet_companion_execution_ports_invalid")
        self._process = None
        self._closed = False

    @property
    def process(self):
        return self._process

    def exchange(self, document, *, cooperate, allow_ports=True):
        if self._closed:
            raise CompanionExecutionError("meet_companion_execution_closed")
        request = (json.dumps(document, ensure_ascii=False) + "\n").encode()
        if len(request) > self._input_limit:
            raise ValueError("meet_companion_execution_request_size")
        try:
            if self._process is None:
                self._process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("owned_child_runtime.py")),
                     str(os.getpid()), *self._command],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=self._environment, close_fds=True, start_new_session=True, bufsize=0,
                )
            process = self._process
            deadline = time.monotonic() + self._timeout
            os.set_blocking(process.stdin.fileno(), False)
            written, sequence, received = 0, 1, 0
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdin, selectors.EVENT_WRITE)
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    cooperate()
                    if time.monotonic() >= deadline:
                        raise CompanionExecutionError("meet_companion_execution_timeout")
                    for key, _events in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                        if key.fileobj is process.stdin:
                            written += os.write(process.stdin.fileno(), request[written:])
                            if written == len(request):
                                selector.unregister(process.stdin)
                        else:
                            chunk = os.read(process.stdout.fileno(), 65_536)
                            if not chunk:
                                raise CompanionExecutionError("meet_companion_execution_failed")
                            output.extend(chunk)
                            received += len(chunk)
                            if received > self._output_limit:
                                raise CompanionExecutionError("meet_companion_execution_output_limit")
                            if b"\n" in output:
                                line, remainder = output.split(b"\n", 1)
                                if remainder or written != len(request):
                                    raise CompanionExecutionError("meet_companion_execution_protocol_invalid")
                                try:
                                    result = decode(line)
                                except (ValueError, UnicodeError, RecursionError):
                                    raise CompanionExecutionError("meet_companion_execution_protocol_invalid") from None
                                if isinstance(result, dict) and result.get("kind") == "port":
                                    try:
                                        if len(line) + 1 > request_limit(result.get("port")):
                                            raise ValueError()
                                        value = validate_request(result, expected_sequence=sequence,
                                                                 ports=self._ports if allow_ports else {})
                                    except ValueError:
                                        raise CompanionExecutionError(
                                            "meet_companion_execution_protocol_invalid"
                                        ) from None
                                    cooperate()
                                    reply = self._ports[value["port"]](value["arguments"], cooperate=cooperate)
                                    cooperate()
                                    if time.monotonic() >= deadline:
                                        raise CompanionExecutionError("meet_companion_execution_timeout")
                                    request = encode({"kind": "port_result", "sequence": sequence, "result": reply})
                                    sequence += 1
                                    written = 0
                                    output.clear()
                                    selector.register(process.stdin, selectors.EVENT_WRITE)
                                    continue
                                return result
        except BaseException:
            self.close()
            raise

    def close(self):
        self._closed = True
        process = self._process
        if process is None:
            return
        try:
            # Kill the owned group even if its original leader exited.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
        finally:
            process.stdin.close()
            process.stdout.close()
