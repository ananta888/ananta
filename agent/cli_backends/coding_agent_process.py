"""Bounded, shell-free process adapter for coding-agent CLIs."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from agent.cli_backends.coding_agent_contract import (
    EventSink,
    ProcessExecutionResult,
)
from agent.cli_backends.coding_agent_process_io import (
    PIPE_FAILURE,
    TERMINATE_GRACE_SECONDS,
    pump_lines,
    terminate_process_group,
    write_input,
)
from agent.cli_backends.coding_agent_process_output import ProcessOutput

_POLL_SECONDS = 0.05


class BoundedCodingAgentProcess:
    """Execute an allowlisted argv with bounded output and process-group cleanup."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        cancellation: threading.Event,
        maximum_output_chars: int,
        input_text: str | None = None,
        event_sink: EventSink | None = None,
        secret_values: Sequence[str] = (),
    ) -> ProcessExecutionResult:
        command = tuple(argv)
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("coding_agent_executable_not_absolute")
        if len(command) > 64 or any(not isinstance(value, str) or not value or "\x00" in value for value in command):
            raise ValueError("coding_agent_argv_invalid")
        workspace = cwd.resolve()
        if not workspace.is_dir():
            raise ValueError("coding_agent_workspace_invalid")
        safe_environment = {str(name): str(value) for name, value in environment.items() if name and "\x00" not in name}
        output = ProcessOutput(maximum_output_chars, event_sink, secret_values)
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - absolute executable from shutil.which
            command,
            cwd=workspace,
            env=safe_environment,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=os.name == "posix",
        )
        messages: queue.Queue = queue.Queue(maxsize=32)
        stopped = threading.Event()
        readers = (
            threading.Thread(target=pump_lines, args=("stdout", process.stdout, messages, stopped), daemon=True),
            threading.Thread(target=pump_lines, args=("stderr", process.stderr, messages, stopped), daemon=True),
        )
        writer = (
            threading.Thread(target=write_input, args=(process.stdin, input_text), daemon=True)
            if input_text is not None and process.stdin is not None
            else None
        )
        started_threads = []
        completed_readers = 0
        reason = "completed"
        forced_return_code: int | None = None
        drain_deadline = None
        try:
            for thread in (*readers, *((writer,) if writer is not None else ())):
                thread.start()
                started_threads.append(thread)
            while completed_readers < len(readers) or process.poll() is None:
                if forced_return_code is None:
                    if cancellation.is_set():
                        reason, forced_return_code = "cancelled", 130
                    elif time.monotonic() - started >= timeout_seconds:
                        reason, forced_return_code = "timeout", 124
                if drain_deadline is None and (forced_return_code is not None or process.poll() is not None):
                    terminate_process_group(process)
                    drain_deadline = time.monotonic() + 1.0
                if drain_deadline is not None and time.monotonic() >= drain_deadline:
                    if forced_return_code is None:
                        reason, forced_return_code = "process_io_failed", 74
                    break
                try:
                    stream, chunk = messages.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                if chunk is PIPE_FAILURE:
                    if forced_return_code is None:
                        reason, forced_return_code = "process_io_failed", 74
                    continue
                if chunk is None:
                    completed_readers += 1
                if not output.feed(stream, chunk) and forced_return_code is None:
                    reason, forced_return_code = "output_limit_exceeded", 65
            return_code = process.wait(timeout=TERMINATE_GRACE_SECONDS)
        finally:
            # Also close the group after successful leader exit with closed pipes.
            try:
                terminate_process_group(process)
            finally:
                stopped.set()
                for thread in started_threads:
                    thread.join(timeout=1.0)
                for thread, stream in zip((*readers, writer), (process.stdout, process.stderr, process.stdin)):
                    if stream is not None and (thread is None or not thread.is_alive()):
                        try:
                            stream.close()
                        except OSError:
                            pass

        if forced_return_code is not None:
            return_code = forced_return_code
        elif return_code != 0:
            reason = "process_failed"
        return ProcessExecutionResult(
            return_code=return_code,
            stdout=output.text("stdout"),
            stderr=output.text("stderr"),
            reason_code=reason,
            duration_ms=int((time.monotonic() - started) * 1000),
            output_truncated=reason == "output_limit_exceeded",
        )


__all__ = ["BoundedCodingAgentProcess"]
