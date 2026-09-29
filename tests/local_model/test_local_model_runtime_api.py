from datetime import UTC, datetime

from agent.repositories.local_model_runtime_decision import SqliteLocalRuntimeDecisionRepository
from agent.services.local_model_runtime_composition import LocalModelRuntimeComposition
from agent.services.local_model_runtime_lifecycle_service import LocalRuntimeLifecycleService
from agent.services.local_model_runtime_status_service import LocalRuntimeStatusService, RuntimeProbeObservation
from agent.services.local_multi_model_runtime import GiB, ResourceSnapshot, rtx3080_local_model_capabilities
from ananta_contracts.local_model_runtime import LocalRuntimeControlReceipt, RuntimeHealth, RuntimeReadiness


class Resources:
    def snapshot(self):
        return ResourceSnapshot(10 * GiB, 10 * GiB, 64 * GiB)


class Probes:
    def probe(self, capability, *, timeout_seconds):
        return RuntimeProbeObservation(
            RuntimeHealth.HEALTHY,
            RuntimeReadiness.READY,
            "runtime_ready",
            (capability.model_id,),
        )


class Control:
    def apply(self, decision, *, action):
        return LocalRuntimeControlReceipt(
            decision_id=decision.decision_id,
            decision_digest=decision.decision_digest,
            action=action,
            status="completed",
            reason_code="runtime_control_completed",
            completed_at="2026-08-27T00:00:00Z",
        )


def _install(app, tmp_path):
    capabilities = rtx3080_local_model_capabilities()
    resources = Resources()
    app.config["ROLE"] = "hub"
    app.extensions["local_model_runtime_composition"] = LocalModelRuntimeComposition(
        capabilities=capabilities,
        status=LocalRuntimeStatusService(
            probes=Probes(),
            resources=resources,
            clock=lambda: datetime(2026, 8, 27, tzinfo=UTC),
        ),
        lifecycle=LocalRuntimeLifecycleService(
            resources=resources,
            decisions=SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3"),
            capabilities=capabilities,
            control=Control(),
        ),
    )


def test_local_runtime_status_is_content_free_and_separates_three_runtimes(client, app, admin_auth_header, tmp_path):
    _install(app, tmp_path)

    response = client.get("/models/local-runtime/v1/status", headers=admin_auth_header)

    assert response.status_code == 200
    assert [item["runtime_id"] for item in response.json["data"]["runtimes"]] == ["kat", "lfm", "needle"]
    assert "prompt" not in response.text.lower()


def test_admin_evaluates_then_applies_exact_decision(client, app, admin_auth_header, tmp_path):
    _install(app, tmp_path)

    evaluated = client.post(
        "/models/local-runtime/v1/decisions",
        json={"request_id": "activate-1"},
        headers=admin_auth_header,
    )
    decision = evaluated.json["data"]
    applied = client.post(
        f"/models/local-runtime/v1/decisions/{decision['decision_id']}/apply",
        json={"action": "activate"},
        headers=admin_auth_header,
    )

    assert evaluated.status_code == 200
    assert decision["admitted"] is True
    assert applied.status_code == 200
    assert applied.json["data"]["decision_digest"] == decision["decision_digest"]


def test_worker_role_cannot_own_local_runtime_decisions(client, app, admin_auth_header, tmp_path):
    _install(app, tmp_path)
    app.config["ROLE"] = "worker"

    response = client.post(
        "/models/local-runtime/v1/decisions",
        json={"request_id": "activate-2"},
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["data"]["reason_code"] == "local_runtime_hub_only"


def test_disabled_runtime_endpoint_does_not_build_implicit_composition(
    client,
    app,
    admin_auth_header,
):
    app.config["ROLE"] = "hub"
    app.extensions.pop("local_model_runtime_composition", None)
    app.extensions.pop("local_model_runtime_wiring_status", None)

    response = client.get("/models/local-runtime/v1/status", headers=admin_auth_header)

    assert response.status_code == 503
    assert "local_model_runtime_composition" not in app.extensions
