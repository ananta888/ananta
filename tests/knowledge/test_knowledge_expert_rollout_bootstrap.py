from __future__ import annotations

from flask import Flask

from agent.bootstrap.knowledge_expert_rollout import initialize_knowledge_expert_rollout


class _GenerationSwitch:
    def switch(self, **_kwargs: str) -> bool:
        return True


def test_rollout_bootstrap_wires_controller_only_on_hub(tmp_path):
    hub = Flask("hub")
    hub.config.update(
        ROLE="hub",
        ANANTA_KNOWLEDGE_EXPERTS_ROLLOUT_STATE=str(tmp_path / "rollout.sqlite3"),
    )
    hub.extensions["knowledge_expert_registry_service"] = _GenerationSwitch()

    status = initialize_knowledge_expert_rollout(hub)

    assert status.ready is True
    assert "knowledge_expert_rollout_controller" in hub.extensions

    worker = Flask("worker")
    worker.config["ROLE"] = "worker"
    status = initialize_knowledge_expert_rollout(worker)
    assert status.ready is False
    assert status.reason_code == "knowledge_expert_rollout_hub_role_required"
    assert "knowledge_expert_rollout_controller" not in worker.extensions


def test_rollout_bootstrap_fails_closed_without_registry():
    hub = Flask("hub")
    hub.config["ROLE"] = "hub"

    status = initialize_knowledge_expert_rollout(hub)

    assert status.ready is False
    assert status.reason_code == "knowledge_expert_registry_unavailable"
    assert "knowledge_expert_rollout_controller" not in hub.extensions
