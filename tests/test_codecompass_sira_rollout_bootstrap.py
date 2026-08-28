from __future__ import annotations

from flask import Flask

from agent.bootstrap.codecompass_sira_rollout import initialize_codecompass_sira_rollout


def test_rollout_bootstrap_wires_persistent_service_only_on_hub(tmp_path):
    hub = Flask("hub")
    hub.config.update(
        ROLE="hub",
        CODECOMPASS_SIRA_ROLLOUT_STATE=str(tmp_path / "rollout.sqlite3"),
    )

    status = initialize_codecompass_sira_rollout(hub)

    assert status.ready is True
    assert "codecompass_sira_rollout_service" in hub.extensions

    worker = Flask("worker")
    worker.config["ROLE"] = "worker"
    status = initialize_codecompass_sira_rollout(worker)
    assert status.ready is False
    assert status.reason_code == "sira_rollout_hub_role_required"
    assert "codecompass_sira_rollout_service" not in worker.extensions
