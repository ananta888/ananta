from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from worker.training.process_control import CancellationToken, ProcessGroupController, TrainingCancelled
from worker.training.subprocess_executor import IsolatedBackendExecutor


def test_cancellation_token_is_cooperative_and_idempotent() -> None:
    token = CancellationToken()
    assert token.cancelled is False

    token.cancel()
    token.cancel()

    assert token.cancelled is True
    with pytest.raises(TrainingCancelled):
        token.raise_if_cancelled()


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX containment primitive")
def test_process_controller_terminates_only_the_job_process_group(tmp_path: Path) -> None:
    controller = ProcessGroupController()
    process = controller.start(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        env=os.environ,
    )
    try:
        assert os.getpgid(process.pid) == process.pid
        result = controller.terminate(process, grace_seconds=1.0)
        assert result.forced is False
        assert result.return_code == -signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX containment primitive")
def test_process_controller_escalates_to_kill_after_grace_period(tmp_path: Path) -> None:
    controller = ProcessGroupController()
    process = controller.start(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(30)",
        ],
        cwd=str(tmp_path),
        env=os.environ,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == b"ready"
        started = time.monotonic()
        result = controller.terminate(process, grace_seconds=0.05)
        assert time.monotonic() - started < 2
        assert result.forced is True
        assert result.return_code == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX containment primitive")
def test_isolated_executor_cancels_the_process_bound_to_an_attempt_token(tmp_path: Path) -> None:
    executor = IsolatedBackendExecutor(termination_grace_seconds=0.1)
    token = CancellationToken()
    process = executor._processes.start(  # noqa: SLF001 - focused token/process binding test
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        env=os.environ,
    )
    executor._running[id(token)] = process  # noqa: SLF001 - focused token/process binding test
    try:
        result = executor.cancel(token)

        assert token.cancelled is True
        assert process.poll() is not None
        assert result is not None
        assert result.forced is False
    finally:
        if process.poll() is None:
            process.kill()
