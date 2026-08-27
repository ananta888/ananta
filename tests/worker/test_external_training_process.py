from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from worker.training.backends.base import TrainingBackendError
from worker.training.external_process import BoundedExternalTrainingProcess
from worker.training.process_control import CancellationToken


def test_runner_removes_secrets_and_forces_offline_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_SECRET", "must-not-cross-process")
    output = tmp_path / "environment.json"
    code = (
        "import json,os,sys;"
        "json.dump({'secret':os.getenv('ANANTA_SECRET'),'offline':os.getenv('HF_HUB_OFFLINE'),"
        "'telemetry':os.getenv('HF_HUB_DISABLE_TELEMETRY')},open(sys.argv[1],'w'))"
    )
    BoundedExternalTrainingProcess(allowed_executable=Path(sys.executable)).run(
        (sys.executable, "-c", code, str(output)),
        cwd=tmp_path,
        cancel=CancellationToken(),
        deadline_epoch_ms=int(time.time() * 1000) + 5_000,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "secret": None,
        "offline": "1",
        "telemetry": "1",
    }


def test_cancellation_terminates_only_the_owned_process_group(tmp_path: Path) -> None:
    token = CancellationToken()
    timer = threading.Timer(0.05, token.cancel)
    timer.start()
    try:
        with pytest.raises(TrainingBackendError) as error:
            BoundedExternalTrainingProcess(
                allowed_executable=Path(sys.executable), poll_seconds=0.02, grace_seconds=0.2
            ).run(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                cwd=tmp_path,
                cancel=token,
                deadline_epoch_ms=int(time.time() * 1000) + 5_000,
            )
    finally:
        timer.cancel()

    assert error.value.code == "cancelled"
    assert error.value.retryable is False
