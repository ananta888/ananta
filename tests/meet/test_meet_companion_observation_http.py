"""Actual signed phase callback HTTP; no Worker phase/publication assertions."""

from types import SimpleNamespace

import pytest

from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import KEY, assignment
from tests.meet.test_meet_companion_task_grant_routes import hub as hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from worker.meet_media.dialog_client import HubDialogClient


def test_observation_http_binds_nonce_and_rejects_worker_phase_or_source_fields(hub, tmp_path, monkeypatch):
    app, fixture, _payload, _key = hub
    requests = []

    def observe(payload):
        requests.append(payload)
        return {"schema": "ananta.meet-dialog-observed.v1", "nonce": payload["nonce"]}

    app.extensions.update(meet_binding_service=fixture.binding, meet_dialog_service=SimpleNamespace(observe=observe))
    key = tmp_path / "worker-key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key))
    with running_hub(app) as endpoint:
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", endpoint.removesuffix("/companion/task-grant") + "/dialog")
        client = HubDialogClient(assignment())
        result = client.call("observe", meet_session_id="ms_" + "a" * 32)
        assert result == {"schema": "ananta.meet-dialog-observed.v1", "nonce": requests[0]["nonce"]}
        assert set(requests[0]) == {"schema", "action", "task_id", "lease_id", "runtime_id", "nonce", "sent_at",
                                    "meet_session_id"}
        for extra in ({"phase": "publishing"}, {"publications": ["camera"]}):
            with pytest.raises(ValueError, match="callback_invalid"):
                client.call("observe", meet_session_id="ms_" + "a" * 32, **extra)
        assert len(requests) == 1
