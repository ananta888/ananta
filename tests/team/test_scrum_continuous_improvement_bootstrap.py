from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.bootstrap.scrum_continuous_improvement import initialize_scrum_continuous_improvement


def test_scrum_services_are_wired_only_on_the_hub(tmp_path):
    hub = Flask("hub")
    hub.config.update(
        ROLE="hub",
        ANANTA_SCRUM_IMPROVEMENT_STATE=str(tmp_path / "scrum.sqlite3"),
    )
    hub.extensions["core_services"] = SimpleNamespace(evolution_service=object())
    status = initialize_scrum_continuous_improvement(hub)
    assert status.ready is True
    assert "scrum_sprint_control_service" in hub.extensions
    assert "scrum_architecture_loop_service" in hub.extensions
    assert "scrum_retrospective_service" in hub.extensions
    assert "scrum_continuous_improvement_query_service" in hub.extensions

    worker = Flask("worker")
    worker.config["ROLE"] = "worker"
    status = initialize_scrum_continuous_improvement(worker)
    assert status.ready is False
    assert status.reason_code == "scrum_improvement_hub_role_required"
    assert "scrum_sprint_control_service" not in worker.extensions
