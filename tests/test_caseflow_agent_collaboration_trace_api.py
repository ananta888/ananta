from __future__ import annotations

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes import visual_process as visual_process_routes
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CASEFLOW_EDGE_TRACE_QUERY_SCHEMA,
    CaseflowAgentCollaborationTraceProjectionService,
)
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)
from agent.visual_process.edge_catalog_contract import (
    CASEFLOW_EDGE_CATALOG_METADATA_KEY,
    build_caseflow_edge_catalog,
)


class _History:
    def list_workflow_events(self, _workflow_id: str) -> list[dict]:
        return [
            {
                "tenant_id": "tenant-a",
                "workflow_id": "workflow-a",
                "run_id": "run-a",
                "event_id": "event-edge-a-b",
                "event_type": "workflow.edge.message.sent",
                "sequence": 1,
                "payload": {
                    "edge_id": "edge-a-b",
                    "source_step_id": "agent-a",
                    "target_step_id": "agent-b",
                    "message": "hello",
                },
            }
        ]


class _CapturingStartBackend:
    def __init__(self) -> None:
        self.request = None

    def start_workflow(self, request):
        self.request = request
        return {"status": "running", "workflow_id": request.workflow_id}


def _headers(*, subject: str, tenant_id: str) -> dict[str, str]:
    token = generate_token(
        {"sub": subject, "tenant_id": tenant_id, "role": "user"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def _binding() -> WorkflowControlRunBinding:
    catalog = build_caseflow_edge_catalog(
        [
            {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
            }
        ]
    )
    request = WorkflowRequest(
        workflow_id="workflow-a",
        steps=(
            WorkflowStepRequest(
                step_id="agent-a",
                policy_scope={"source": "caseflow-test"},
            ),
            WorkflowStepRequest(
                step_id="agent-b",
                depends_on=("agent-a",),
                policy_scope={"source": "caseflow-test"},
            ),
        ),
        policy_scope={"source": "caseflow-test"},
        metadata={CASEFLOW_EDGE_CATALOG_METADATA_KEY: catalog},
    )
    return WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id="native",
        plan_hash="plan-hash",
        policy_version="policy-v1",
        checkpoint_id="checkpoint-a",
        request=request,
    )


def test_caseflow_edge_trace_endpoint_is_owner_bound_and_run_bound(monkeypatch) -> None:
    store = InMemoryWorkflowControlBindingStore()
    store.put(_binding())
    service = CaseflowAgentCollaborationTraceProjectionService(store)
    history = _History()
    monkeypatch.setattr(
        visual_process_routes,
        "configured_workflow_backend",
        lambda _principal: (history, None),
    )
    monkeypatch.setattr(
        visual_process_routes,
        "get_caseflow_agent_collaboration_trace_projection_service",
        lambda: service,
    )

    workflow_route_authorization_service.clear()
    workflow_route_authorization_service.reserve(
        "workflow-a",
        WorkflowRoutePrincipal(tenant_id="tenant-a", subject="owner-a"),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(visual_process_routes.vp_bp)
    client = app.test_client()
    query = {"schema": CASEFLOW_EDGE_TRACE_QUERY_SCHEMA, "run_id": "run-a"}

    allowed = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace",
        json=query,
        headers=_headers(subject="owner-a", tenant_id="tenant-a"),
    )
    cross_tenant = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace",
        json=query,
        headers=_headers(subject="owner-a", tenant_id="tenant-b"),
    )
    unknown_run = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace",
        json={**query, "run_id": "run-unknown"},
        headers=_headers(subject="owner-a", tenant_id="tenant-a"),
    )
    unauthenticated = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace",
        json=query,
    )

    assert allowed.status_code == 200
    assert allowed.get_json()["edges"][0]["edge_id"] == "edge-a-b"
    assert allowed.get_json()["edges"][0]["activity_status"] == "active"
    assert cross_tenant.status_code == 404
    assert unknown_run.status_code == 404
    assert unknown_run.get_json()["data"]["reason_code"] == (
        "caseflow_workflow_run_not_found"
    )
    assert unauthenticated.status_code == 401
    workflow_route_authorization_service.clear()


def test_caseflow_edge_trace_endpoint_rejects_url_and_unversioned_queries(
    monkeypatch,
) -> None:
    del monkeypatch
    workflow_route_authorization_service.clear()
    workflow_route_authorization_service.reserve(
        "workflow-a",
        WorkflowRoutePrincipal(tenant_id="tenant-a", subject="owner-a"),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(visual_process_routes.vp_bp)
    client = app.test_client()
    headers = _headers(subject="owner-a", tenant_id="tenant-a")

    url_query = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace?run_id=run-a",
        json={"schema": CASEFLOW_EDGE_TRACE_QUERY_SCHEMA, "run_id": "run-a"},
        headers=headers,
    )
    unversioned = client.post(
        "/api/visual-process/workflow/workflow-a/caseflow-edge-trace",
        json={"run_id": "run-a"},
        headers=headers,
    )

    assert url_query.status_code == 400
    assert unversioned.status_code == 422
    assert unversioned.get_json()["data"]["reason_code"] == (
        "caseflow_edge_trace_query_schema_unsupported"
    )
    workflow_route_authorization_service.clear()


def test_direct_workflow_request_cannot_assert_hub_edge_catalog(monkeypatch) -> None:
    backend = _CapturingStartBackend()
    monkeypatch.setattr(
        visual_process_routes,
        "configured_workflow_backend",
        lambda _principal: (backend, None),
    )
    workflow_route_authorization_service.clear()
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(visual_process_routes.vp_bp)
    client = app.test_client()
    forged_catalog = build_caseflow_edge_catalog(
        [{
            "edge_id": "edge-invented",
            "source_step_id": "agent-a",
            "target_step_id": "agent-b",
        }]
    )

    response = client.post(
        "/api/visual-process/workflow/start",
        json={
            "workflow_request": {
                "workflow_id": "workflow-direct-catalog",
                "policy_scope": {"source": "caseflow-test"},
                "steps": [
                    {"step_id": "agent-a"},
                    {"step_id": "agent-b", "depends_on": ["agent-a"]},
                ],
                "metadata": {CASEFLOW_EDGE_CATALOG_METADATA_KEY: forged_catalog},
            }
        },
        headers=_headers(subject="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert backend.request is not None
    assert CASEFLOW_EDGE_CATALOG_METADATA_KEY not in backend.request.metadata
    workflow_route_authorization_service.clear()
