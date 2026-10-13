"""Real executor/replay/watchdog over synthetic run adapter and real signed HTTP."""

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import KEY, assignment
from tests.meet.test_meet_companion_task_grant_routes import hub as hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from worker.meet_media.dialog_executor import DialogExecutor


@pytest.mark.timeout(15)
@pytest.mark.parametrize("mode", ["complete", "cancel", "stall"])
def test_assigned_child_progress_and_terminal_are_bounded_by_actual_executor(hub, tmp_path, monkeypatch, mode):
    app, fixture, _payload, _key = hub
    finishes = []

    def finish(payload):
        assert tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id")) == ("task", "dispatch", "runtime")
        finishes.append(payload["status"])
        return {"schema": "ananta.meet-dialog-finished.v1", "nonce": payload["nonce"]}

    app.extensions["meet_dialog_service"] = SimpleNamespace(finish=finish)
    app.extensions["meet_binding_service"] = fixture.binding
    key = tmp_path / "worker-key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    ready = tmp_path / "ready"
    original_spawn = subprocess.Popen
    children = []

    def synthetic_run_adapter(command, **options):
        assert command == [sys.executable, "-m", "worker.meet_media.companion_task_runtime"]
        # Explicitly replace only browser execution with the declared probe;
        # actual assignment input, entrypoint, HTTP and progress ports remain.
        child = original_spawn([sys.executable, str(Path(__file__).with_name(
            "meet_companion_assigned_process_probe.py")), mode, str(ready)], **options)
        children.append(child)
        return child

    with running_hub(app) as endpoint:
        monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key))
        monkeypatch.setenv("MEET_HUB_COMPANION_TASK_URL", endpoint)
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", endpoint.removesuffix("/companion/task-grant") + "/dialog")
        monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        monkeypatch.setattr("worker.meet_media.dialog_executor.subprocess.Popen", synthetic_run_adapter)
        executor = DialogExecutor(tmp_path / "replay.sqlite", slots=1)
        value = assignment() | {"session_id": "hub-session", "deadline": fixture.context["deadline"]}
        try:
            assert executor.start(value)["status"] == "accepted"
            bound = time.monotonic() + 5
            while not ready.exists():
                assert time.monotonic() < bound and children[0].poll() is None, "bounded child startup failed"
                time.sleep(0.01)
            if mode == "cancel":
                fixture.task.status = "cancelled"
            started = time.monotonic()
            children[0].wait(timeout=4)
            assert time.monotonic() - started < 4
            assert children[0].returncode == {"complete": 0, "cancel": 1, "stall": -9}[mode]
            bound = time.monotonic() + 1
            while executor.slots.snapshot()["active"]:
                assert time.monotonic() < bound, "parent slot was not released"
                time.sleep(0.01)
            assert finishes == {"complete": ["completed"], "cancel": ["failed"], "stall": []}[mode]
            with pytest.raises(ValueError, match="replayed"):
                executor.start(value)
            assert len(children) == 1
        finally:
            for child in children:
                if child.poll() is None:
                    DialogExecutor._stop(child)
