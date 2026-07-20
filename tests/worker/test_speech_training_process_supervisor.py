from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from worker.speech_training.backend import AbortSignal, SpeechTrainingAborted, SpeechTrainingBackendError
from worker.speech_training.process_supervisor import BoundedSpeechChildProcess


def test_abort_terminates_stubborn_process_group_with_hard_kill(tmp_path) -> None:
    supervisor = BoundedSpeechChildProcess(
        allowed_executables=(Path(sys.executable),),
        poll_interval_seconds=0.01,
        termination_grace_seconds=0.05,
    )
    abort = AbortSignal()
    program = (
        "import signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(3600)']);"
        "time.sleep(3600)"
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            supervisor.run,
            (sys.executable, "-c", program),
            cwd=tmp_path,
            abort=abort,
            timeout_seconds=10,
        )
        deadline = time.monotonic() + 5
        while supervisor.active_pid is None and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = supervisor.active_pid
        assert pid is not None
        abort.abort("speech_training_cancelled")
        with pytest.raises(SpeechTrainingAborted, match="speech training execution was aborted|lost its Hub") as error:
            pending.result(timeout=5)

    assert error.value.reason_code == "speech_training_cancelled"
    assert supervisor.active_pid is None and supervisor.last_pid == pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_executable_and_environment_are_closed_allowlists(tmp_path) -> None:
    supervisor = BoundedSpeechChildProcess(allowed_executables=(Path(sys.executable),))
    with pytest.raises(SpeechTrainingBackendError) as executable:
        supervisor.run(
            ("/bin/sh", "-c", "exit 0"),
            cwd=tmp_path,
            abort=AbortSignal(),
            timeout_seconds=1,
        )
    assert executable.value.reason_code == "speech_child_process_executable_forbidden"
    with pytest.raises(SpeechTrainingBackendError) as environment:
        supervisor.run(
            (sys.executable, "-c", "pass"),
            cwd=tmp_path,
            abort=AbortSignal(),
            timeout_seconds=1,
            environment={"AWS_SECRET_ACCESS_KEY": "forbidden"},
        )
    assert environment.value.reason_code == "speech_child_process_environment_forbidden"
