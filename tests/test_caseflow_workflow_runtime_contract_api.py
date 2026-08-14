from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes import visual_process as visual_process_routes
from agent.routes import workflow_control_security
from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA, WorkflowRequest
from agent.services.workflow_control_composition import (
    WorkflowBackendControlFacade,
    build_workflow_backend_control_facade,
)
from agent.services.workflow_route_authorization_service import (
    workflow_route_authorization_service,
)
from agent.visual_process.definition_snapshot_contract import (
    VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY,
)
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA

WORKFLOW_INPUT_ID = "caseflow-runtime-contract"
CONTROL_RUN_INPUT_ID = "caseflow-runtime-control-binding"
TEMPORAL_SOURCE_REVISION = 7


class _RecordingTemporalInfrastructureAdapter:
    """Record infrastructure calls without fabricating Hub-owned fields."""

    backend_id = "temporal"

    def __init__(self) -> None:
        self.started_requests: list[WorkflowRequest] = []
        self.status_queries: list[str] = []
        self.history_queries: list[str] = []

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        self.started_requests.append(request)
        # Temporal's start ACK is backend-shaped, not the workflow query
        # contract. The Hub must supply its own control binding and revision.
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "temporal",
            "workflow_id": request.workflow_id,
            "status": "running",
            "correlation_id": request.correlation_id,
            "events": [],
        }

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self.status_queries.append(workflow_id)
        return self._query_observation()

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        self.history_queries.append(workflow_id)
        return []

    def _query_observation(self) -> dict[str, Any]:
        # Exact field shape emitted by Temporal WorkflowStatus.to_dict().
        request = self.started_requests[-1]
        return {
            "schema": TEMPORAL_STATUS_SCHEMA,
            "workflow_id": request.workflow_id,
            "run_id": request.metadata["run_id"],
            "status": "waiting_approval",
            "revision": TEMPORAL_SOURCE_REVISION,
            "current_step_id": "critic",
            "completed_step_ids": ["lead", "builder"],
            "retry_budget_remaining": 2,
            "checkpoint_ref": f"temporal:{request.workflow_id}:{TEMPORAL_SOURCE_REVISION}",
            "open_gates": ["critic"],
            "reason_code": "",
            "parameters": {},
            "plan_hash": request.metadata["plan_hash"],
            "plan_revision": 1,
            "plan_ref": "",
            "active_step_ids": [],
            "failed_step_ids": [],
        }


class _AllowRuntimeRelease:
    def evaluate(self, **_values: Any) -> tuple[bool, str]:
        return True, "runtime_release_caseflow_contract_admitted"


class _NoOpReadModelProjector:
    def project(self, **_values: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_workflow_route_authorization() -> Iterator[None]:
    workflow_route_authorization_service.set_owner_resolver(None)
    workflow_route_authorization_service.clear()
    yield
    workflow_route_authorization_service.set_owner_resolver(None)
    workflow_route_authorization_service.clear()


def _headers(*, subject: str, tenant_id: str) -> dict[str, str]:
    token = generate_token(
        {"sub": subject, "tenant_id": tenant_id, "role": "user"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def _workflow_payload() -> dict[str, Any]:
    return {
        "workflow_request": {
            "workflow_id": WORKFLOW_INPUT_ID,
            "workflow_type": "caseflow_agent_collaboration",
            "steps": [
                {
                    "step_id": "lead",
                    "task_kind": "coding",
                    "policy_scope": {"source": "caseflow-contract-test"},
                },
                {
                    "step_id": "builder",
                    "task_kind": "coding",
                    "depends_on": ["lead"],
                    "policy_scope": {"source": "caseflow-contract-test"},
                },
                {
                    "step_id": "critic",
                    "task_kind": "review",
                    "depends_on": ["builder"],
                    "gate": True,
                    "policy_scope": {"source": "caseflow-contract-test"},
                },
            ],
            "policy_scope": {"source": "caseflow-contract-test"},
            "metadata": {
                "run_id": CONTROL_RUN_INPUT_ID,
                VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY: "f" * 64,
            },
        }
    }


def _assert_runtime_contract(
    payload: dict[str, Any],
    *,
    facade: WorkflowBackendControlFacade,
    expected_revision: int,
) -> None:
    binding = facade.bindings.get(WORKFLOW_INPUT_ID)
    stored = facade.bindings.last_status(WORKFLOW_INPUT_ID)

    assert binding is not None
    assert stored is not None
    assert binding.workflow_id == WORKFLOW_INPUT_ID
    assert binding.run_id == CONTROL_RUN_INPUT_ID
    assert payload["schema"] == WORKFLOW_STATUS_SCHEMA
    assert payload["workflow_id"] == binding.workflow_id
    assert payload["run_id"] == binding.run_id
    assert payload["revision"] == stored["revision"] == expected_revision
    assert payload["plan_hash"] == binding.plan_hash
    assert math.isfinite(payload["updated_at"])
    assert payload["updated_at"] >= 0
    assert payload["status"] == "waiting_for_approval"
    assert payload["source_observation"] == {
        "schema": TEMPORAL_STATUS_SCHEMA,
        "status": "waiting_approval",
        "revision": TEMPORAL_SOURCE_REVISION,
    }
    assert payload["steps"] == [
        {"step_id": "lead", "status": "completed"},
        {"step_id": "builder", "status": "completed"},
        {"step_id": "critic", "status": "waiting_for_approval"},
    ]
    assert require_canonical_identity(payload["run_id"], field_name="run_id") == binding.run_id


def test_caseflow_workflow_start_and_status_use_authoritative_hub_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infrastructure = _RecordingTemporalInfrastructureAdapter()
    facade = build_workflow_backend_control_facade(
        infrastructure,
        ownership=workflow_route_authorization_service,
        release_admission=_AllowRuntimeRelease(),
        read_model_projector=_NoOpReadModelProjector(),
    )
    monkeypatch.setattr(
        workflow_control_security,
        "get_workflow_backend_control_facade",
        lambda: facade,
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(visual_process_routes.vp_bp)
    client = app.test_client()
    owner = _headers(subject="caseflow-owner", tenant_id="caseflow-tenant")

    started = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload(),
        headers=owner,
    )

    assert started.status_code == 200
    started_payload = started.get_json()
    # Durable start completion is acknowledged only after the Hub has fetched
    # and projected the authoritative Temporal observation. The route therefore
    # never exposes the backend-shaped rev0 start ACK as runtime truth.
    assert started_payload["status"] == "waiting_for_approval"
    _assert_runtime_contract(
        started_payload,
        facade=facade,
        expected_revision=TEMPORAL_SOURCE_REVISION,
    )
    assert len(infrastructure.started_requests) == 1
    dispatched = infrastructure.started_requests[0]
    assert dispatched.requested_by == "caseflow-owner"
    assert dispatched.metadata["tenant_id"] == "caseflow-tenant"
    assert dispatched.metadata["run_id"] == CONTROL_RUN_INPUT_ID
    assert VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY not in dispatched.metadata
    assert "snapshot_hash" not in started_payload

    reconciliation = facade.reconcile_active()
    status = client.get(
        f"/api/visual-process/workflow/{WORKFLOW_INPUT_ID}/status",
        headers=owner,
    )

    assert reconciliation["runtime_ids"] == ["temporal"]
    assert reconciliation["processed"] == 1
    assert reconciliation["failed"] == []
    assert status.status_code == 200
    _assert_runtime_contract(
        status.get_json(),
        facade=facade,
        expected_revision=TEMPORAL_SOURCE_REVISION,
    )
    assert infrastructure.status_queries == [WORKFLOW_INPUT_ID, WORKFLOW_INPUT_ID]
    assert infrastructure.history_queries == [WORKFLOW_INPUT_ID]

    adapter_calls = (
        len(infrastructure.started_requests),
        list(infrastructure.status_queries),
        list(infrastructure.history_queries),
    )
    foreign_headers = _headers(
        subject="caseflow-foreign",
        tenant_id="caseflow-tenant",
    )
    foreign_status = client.get(
        f"/api/visual-process/workflow/{WORKFLOW_INPUT_ID}/status",
        headers=foreign_headers,
    )
    foreign_start = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload(),
        headers=foreign_headers,
    )
    unauthenticated_status = client.get(
        f"/api/visual-process/workflow/{WORKFLOW_INPUT_ID}/status",
    )
    unauthenticated_start = client.post(
        "/api/visual-process/workflow/start",
        json=_workflow_payload(),
    )

    assert foreign_status.status_code == 404
    assert foreign_status.get_json()["data"]["reason_code"] == "workflow_run_not_found"
    assert foreign_start.status_code == 409
    assert foreign_start.get_json()["data"]["reason_code"] == "workflow_id_unavailable"
    assert unauthenticated_status.status_code == 401
    assert unauthenticated_start.status_code == 401
    assert adapter_calls == (
        len(infrastructure.started_requests),
        infrastructure.status_queries,
        infrastructure.history_queries,
    )
