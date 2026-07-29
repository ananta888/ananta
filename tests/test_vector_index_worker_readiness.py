from __future__ import annotations

import pytest
from flask import Flask

import agent.ai_agent as ai_agent
import agent.routes.worker_vector_index_readiness as readiness_route
from worker.retrieval.vector_index_worker_readiness import (
    REQUIRED_VECTOR_INDEX_CAPABILITIES,
    VectorIndexWorkerReadinessPolicy,
)


def _hub_registration(
    *,
    capabilities: tuple[str, ...] = (
        "retrieval",
        "index_write",
        "vector_index_operation",
    ),
    last_success_at: float = 995.0,
) -> dict[str, object]:
    return {
        "enabled": True,
        "registered_as": "worker-alpha",
        "registered_capabilities": list(capabilities),
        "last_success_at": last_success_at,
    }


def _worker_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    vector_ready: bool = True,
    advertised_capabilities: tuple[str, ...] = (
        "retrieval",
        "index_write",
        "vector_index_operation",
    ),
    hub_registration: dict[str, object] | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(
        ROLE="worker",
        AGENT_NAME="worker-alpha",
    )
    app.extensions["vector_index_worker_registration"] = {
        "ready": vector_ready,
        "reason_code": (
            None
            if vector_ready
            else "vector_index_task_verification_keyring_required"
        ),
    }
    app.extensions["workflow_adapter_worker_registration"] = {
        "capabilities": list(advertised_capabilities),
    }
    monkeypatch.setattr(
        readiness_route,
        "get_registration_state",
        lambda: (
            hub_registration
            if hub_registration is not None
            else _hub_registration()
        ),
    )
    monkeypatch.setattr(readiness_route.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        readiness_route.settings,
        "agent_offline_timeout",
        300,
    )
    app.register_blueprint(
        readiness_route.worker_vector_index_readiness_bp
    )
    return app


def test_vector_index_worker_readiness_returns_503_before_hub_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _worker_app(
        monkeypatch,
        hub_registration={
            "enabled": True,
            "registered_as": "worker-alpha",
            "registered_capabilities": [],
            "last_success_at": None,
        },
    )

    response = app.test_client().get(
        "/internal/worker/vector-index-readiness"
    )

    assert response.status_code == 503
    assert response.json["ready"] is False
    assert response.json["hub_registration"]["ready"] is False
    assert (
        "vector_index_worker_hub_registration_pending"
        in response.json["reason_codes"]
    )


def test_vector_index_worker_readiness_returns_503_for_local_composition_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _worker_app(monkeypatch, vector_ready=False)

    response = app.test_client().get(
        "/internal/worker/vector-index-readiness"
    )

    assert response.status_code == 503
    assert response.json["vector_index_worker_registration"] == {
        "ready": False,
        "reason_code": (
            "vector_index_task_verification_keyring_required"
        ),
    }


@pytest.mark.parametrize(
    ("advertised", "registered", "reason_code"),
    [
        (
            ("retrieval", "index_write"),
            REQUIRED_VECTOR_INDEX_CAPABILITIES,
            "vector_index_worker_capabilities_not_advertised",
        ),
        (
            REQUIRED_VECTOR_INDEX_CAPABILITIES,
            ("retrieval", "index_write"),
            "vector_index_worker_hub_capabilities_incomplete",
        ),
    ],
)
def test_vector_index_worker_readiness_requires_all_three_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    advertised: tuple[str, ...],
    registered: tuple[str, ...],
    reason_code: str,
) -> None:
    app = _worker_app(
        monkeypatch,
        advertised_capabilities=advertised,
        hub_registration=_hub_registration(
            capabilities=registered
        ),
    )

    response = app.test_client().get(
        "/internal/worker/vector-index-readiness"
    )

    assert response.status_code == 503
    assert reason_code in response.json["reason_codes"]


def test_vector_index_worker_readiness_rejects_stale_hub_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _worker_app(
        monkeypatch,
        hub_registration=_hub_registration(last_success_at=699.0),
    )

    response = app.test_client().get(
        "/internal/worker/vector-index-readiness"
    )

    assert response.status_code == 503
    assert response.json["hub_registration"]["ready"] is False
    assert (
        "vector_index_worker_hub_registration_stale"
        in response.json["reason_codes"]
    )


def test_vector_index_worker_readiness_returns_200_after_exact_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _worker_app(monkeypatch)

    response = app.test_client().get(
        "/internal/worker/vector-index-readiness"
    )

    assert response.status_code == 200
    assert response.json == {
        "status": "ready",
        "ready": True,
        "reason_codes": [],
        "required_capabilities": [
            "retrieval",
            "index_write",
            "vector_index_operation",
        ],
        "advertised_capabilities": [
            "index_write",
            "retrieval",
            "vector_index_operation",
        ],
        "registered_capabilities": [
            "index_write",
            "retrieval",
            "vector_index_operation",
        ],
        "vector_index_worker_registration": {
            "ready": True,
            "reason_code": None,
        },
        "hub_registration": {
            "ready": True,
            "registered_as": "worker-alpha",
        },
    }


def test_policy_rejects_non_worker_role() -> None:
    snapshot = VectorIndexWorkerReadinessPolicy().evaluate(
        role="hub",
        agent_name="worker-alpha",
        vector_registration={"ready": True},
        advertised_capabilities=REQUIRED_VECTOR_INDEX_CAPABILITIES,
        hub_registration=_hub_registration(),
        now=1000.0,
        registration_max_age_seconds=300,
    )

    assert snapshot.ready is False
    assert snapshot.reason_codes == (
        "vector_index_worker_role_required",
    )


def test_hub_role_does_not_register_worker_readiness_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_agent.settings, "role", "hub")
    app = Flask(__name__)

    ai_agent._register_worker_domain_handlers(app)

    assert (
        "/internal/worker/vector-index-readiness"
        not in {rule.rule for rule in app.url_map.iter_rules()}
    )
