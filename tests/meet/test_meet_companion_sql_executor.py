"""Real SQL admission, executor child and signed callbacks; synthetic browser run."""

import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agent.services.meet_companion_task_grants import MeetCompanionTaskGrants
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from tests.meet.test_meet_companion_profile_dispatch import companion_system
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import KEY
from worker.meet_media.dialog_executor import DialogExecutor


class LocalExecutorPort:
    """Test-only local dispatch adapter; replaces the Worker HTTP transport."""

    def __init__(self, executor):
        self.executor = executor
        self.assignment = None

    def start_dialog(self, assignment):
        self.assignment = assignment
        return self.executor.start(assignment)


@pytest.mark.timeout(30)
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("observe", [False, True])
def test_sql_admitted_companion_executes_and_terminal_callback_preserves_hub_task_state(
    app, tmp_path, monkeypatch, cancel, observe,
):
    with app.app_context():
        f = companion_system()
        if observe:
            from agent.repositories.meet_dialog_phases import TaskDialogPhases
            from agent.services.meet_dialog_phases import MeetDialogPhases
            from agent.services.task_runtime_service import compare_and_set_local_task_status

            store = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
            f.service.phases = MeetDialogPhases(store, f.f.authority, f.meet, clock=lambda: f.f.now)
            f.meet.observe.return_value = {
                "lease": {"sessionId": "ms_" + "a" * 32, "expiresAt": (f.f.now + 120) * 1000},
                "peerId": "a" * 16, "publicationRevision": 1, "publications": [],
            }
        private = tmp_path / "hub.pem"
        private.write_bytes(Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        private.chmod(0o600)
        f.service.issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private)
        app.extensions.update(meet_binding_service=f.f.binding, meet_dialog_service=f.service,
                              meet_media_worker_key=KEY,
                              meet_companion_task_grants=MeetCompanionTaskGrants(f.f.authority, f.service.issuer))
    monkeypatch.setitem(app.config, "ROLE", "hub")
    key = tmp_path / "worker-key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    marker = tmp_path / "ready"
    original_spawn = subprocess.Popen
    children = []

    def synthetic_browser_run(command, **options):
        assert command == [sys.executable, "-m", "worker.meet_media.companion_task_runtime"]
        mode = ("observe-" if observe else "") + ("cancel" if cancel else "complete")
        child = original_spawn([sys.executable, str(Path(__file__).with_name(
            "meet_companion_assigned_process_probe.py")), mode, str(marker)], **options)
        children.append(child)
        return child

    with running_hub(app) as endpoint:
        monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key))
        monkeypatch.setenv("MEET_HUB_COMPANION_TASK_URL", endpoint)
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", endpoint.removesuffix("/companion/task-grant") + "/dialog")
        monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        monkeypatch.setattr("worker.meet_media.dialog_executor.subprocess.Popen", synthetic_browser_run)
        executor = DialogExecutor(tmp_path / "replay.sqlite", slots=1)
        # Local test dispatch port: no Worker endpoint/container claimed here.
        f.service.worker = executor_adapter = LocalExecutorPort(executor)
        try:
            with app.app_context():
                admitted = f.service.start(f.principal, "project", f.payload)
            bound = time.monotonic() + 5
            while not marker.exists():
                assert time.monotonic() < bound and children[0].poll() is None, "SQL child startup failed"
                time.sleep(0.01)
            if cancel:
                with app.app_context():
                    stopped = f.service.inspect(f.principal, "project", admitted["task_id"], stop=True)
                    assert stopped["status"] == "cancelled"
            children[0].wait(timeout=5)
            assert children[0].returncode == (1 if cancel else 0)
            with app.app_context():
                task = f.tasks.get_by_id(admitted["task_id"])
                assert task.status == ("cancelled" if cancel else "completed")
                assert task.worker_execution_context["meet_dialog"]["companion_execution"] is True
                if observe:
                    phase = task.worker_execution_context["meet_phase"]
                    assert phase["membership"] == {"session_id": "ms_" + "a" * 32, "peer_id": "a" * 16}
                    assert phase["observation"]["sources"] == []
                    f.meet.observe.assert_called_with(task.id, executor_adapter.assignment["lease_id"],
                                                      executor_adapter.assignment["runtime_id"], "ms_" + "a" * 32)
            assert f.service.worker is executor_adapter and len(children) == 1
            bound = time.monotonic() + 1
            while executor.slots.snapshot()["active"]:
                assert time.monotonic() < bound, "SQL executor slot was not released"
                time.sleep(0.01)
            with pytest.raises(ValueError, match="replayed"):
                executor.start(executor_adapter.assignment)
            assert len(children) == 1
        finally:
            for child in children:
                if child.poll() is None:
                    DialogExecutor._stop(child)
