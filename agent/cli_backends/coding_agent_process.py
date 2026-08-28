"""Bounded, shell-free process adapter for coding-agent CLIs."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Mapping, Sequence

from agent.cli_backends.coding_agent_contract import (
    CodingAgentEvent,
    EventSink,
    ProcessExecutionResult,
)

_POLL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 5.0


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
        redactions = tuple(value for value in secret_values if len(value) >= 4)
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
        if input_text is not None and process.stdin is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except BrokenPipeError:
                pass

        messages: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=1024)
        readers = (
            threading.Thread(target=_pump, args=("stdout", process.stdout, messages), daemon=True),
            threading.Thread(target=_pump, args=("stderr", process.stderr, messages), daemon=True),
        )
        for reader in readers:
            reader.start()

        stdout: list[str] = []
        stderr: list[str] = []
        collected = 0
        sequence = 0
        completed_readers = 0
        reason = "completed"
        forced_return_code: int | None = None
        try:
            while completed_readers < len(readers) or process.poll() is None:
                if cancellation.is_set():
                    reason, forced_return_code = "cancelled", 130
                    _terminate_process_group(process)
                elif time.monotonic() - started >= timeout_seconds:
                    reason, forced_return_code = "timeout", 124
                    _terminate_process_group(process)
                try:
                    message = messages.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                if message is None:
                    completed_readers += 1
                    continue
                stream, raw_text = message
                text = _redact(raw_text, redactions)
                if collected + len(text) > maximum_output_chars:
                    reason, forced_return_code = "output_limit_exceeded", 65
                    _terminate_process_group(process)
                    continue
                collected += len(text)
                (stdout if stream == "stdout" else stderr).append(text)
                sequence += 1
                if event_sink is not None:
                    try:
                        event_sink(CodingAgentEvent(sequence=sequence, stream=stream, text=text.rstrip("\n")))
                    except Exception:
                        pass
            return_code = process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        finally:
            if process.poll() is None:
                _terminate_process_group(process)
            for reader in readers:
                reader.join(timeout=1.0)

        if forced_return_code is not None:
            return_code = forced_return_code
        elif return_code != 0:
            reason = "process_failed"
        return ProcessExecutionResult(
            return_code=return_code,
            stdout="".join(stdout),
            stderr="".join(stderr),
            reason_code=reason,
            duration_ms=int((time.monotonic() - started) * 1000),
            output_truncated=reason == "output_limit_exceeded",
        )


def _pump(stream_name: str, stream: IO[str] | None, messages: queue.Queue[tuple[str, str] | None]) -> None:
    if stream is not None:
        try:
            for line in iter(stream.readline, ""):
                messages.put((stream_name, line))
        finally:
            stream.close()
    messages.put(None)


def _redact(value: str, secrets: Sequence[str]) -> str:
    result = value
    for secret in secrets:
        result = result.replace(secret, "<redacted>")
    return result


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - Windows compatibility
            process.terminate()
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows compatibility
            process.kill()
    except ProcessLookupError:
        return


__all__ = ["BoundedCodingAgentProcess"]
