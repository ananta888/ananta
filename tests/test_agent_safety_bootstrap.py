from __future__ import annotations

from flask import Flask

from agent.bootstrap.agent_safety import initialize_agent_safety
from agent.services.agent_safety_ports import RecordingSafetyAdapter


def test_agent_safety_is_hub_owned_and_default_runtime_fails_closed(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    hub.config.update(ROLE="hub", ANANTA_AGENT_SAFETY_STATE=str(tmp_path / "safety.sqlite3"))
    status = initialize_agent_safety(hub)
    assert status.ready is True
    assert status.containment_available is False
    assert "agent_safety_control_service" in hub.extensions

    worker = Flask("worker")
    worker.secret_key = "test-secret"
    worker.config["ROLE"] = "worker"
    status = initialize_agent_safety(worker)
    assert status.ready is False
    assert status.reason_code == "agent_safety_hub_role_required"
    assert "agent_safety_control_service" not in worker.extensions


def test_agent_safety_accepts_complete_automatic_containment_composition(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    hub.config.update(ROLE="hub", ANANTA_AGENT_SAFETY_STATE=str(tmp_path / "safety.sqlite3"))
    adapter = RecordingSafetyAdapter()
    status = initialize_agent_safety(
        hub,
        sandbox_control=adapter,
        egress_fence=adapter,
        credential_revocation=adapter,
    )
    assert status.ready is True
    assert status.containment_available is True
