from __future__ import annotations

from flask import Flask

from agent.bootstrap.local_model_runtime import initialize_local_model_runtime_services


def test_bootstrap_installs_provider_neutral_observation_port_for_enabled_hub(
    tmp_path,
) -> None:
    app = Flask(__name__)
    app.config.update(
        ROLE="hub",
        ANANTA_LOCAL_MODEL_RUNTIME_ENABLED=True,
        ANANTA_LOCAL_MODEL_STATE_DB=str(tmp_path / "runtime.sqlite3"),
    )

    status = initialize_local_model_runtime_services(app)

    assert status.ready is True
    assert (
        app.extensions["model_invocation_observation_port"]
        is app.extensions["local_model_runtime_composition"].invocations
    )
    assert app.extensions["tiny_router_telemetry_sink"] is app.extensions["local_model_runtime_composition"].invocations


def test_bootstrap_is_disabled_on_worker() -> None:
    app = Flask(__name__)
    app.config.update(ROLE="worker", ANANTA_LOCAL_MODEL_RUNTIME_ENABLED=True)

    status = initialize_local_model_runtime_services(app)

    assert status.reason_code == "local_runtime_hub_role_required"
    assert "model_invocation_observation_port" not in app.extensions
