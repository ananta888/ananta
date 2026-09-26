"""Synthetic read-only admission observations; not production release evidence."""

from dataclasses import replace

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.visual_process import vp_bp
from agent.services.bpmn_workflow_preflight import workflow_start_plan
from agent.services.workflow_control_composition import build_workflow_backend_control_facade
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)
from agent.services.workflow_runtime_selection_composition import build_configured_workflow_runtime_selection
from agent.services.workflow_runtime_selection_service import InMemoryRuntimeSelectionAudit, RuntimeHealthSnapshot
from agent.visual_process.bpmn_adapter import import_bpmn_xml
from tests.bpmn.test_execution_routing import compile_request, xor_xml
from tests.test_workflow_control_composition import RecordingBackend, _NoOpReadModelProjector


class ObservedGates:
    status = "ready"
    released = True

    def __init__(self):
        self.plans = []

    def get_health(self, runtime_id):
        return RuntimeHealthSnapshot(runtime_id, self.status)

    def evaluate(self, **values):
        self.plans.append(values["plan"])
        return self.released, "runtime_release_verified" if self.released else "runtime_release_evidence_unavailable"


@pytest.fixture
def preflight_setup(monkeypatch):
    monkeypatch.setenv("ANANTA_BPMN_EXECUTION_ENABLED", "true")
    owner = WorkflowRoutePrincipal("preflight-tenant", "preflight-owner", roles=("user",))
    ownership = workflow_route_authorization_service
    ownership.clear()
    gates = ObservedGates()
    audit = InMemoryRuntimeSelectionAudit()
    backend = RecordingBackend()
    facade = build_workflow_backend_control_facade(
        backend,
        ownership=ownership,
        release_admission=gates,
        runtime_health=gates,
        runtime_selection_audit=audit,
        read_model_projector=_NoOpReadModelProjector(),
    )
    # Keep production candidate/gate logic, while the execution adapter is a
    # recording test double that cannot create Tasks or perform network I/O.
    selector = build_configured_workflow_runtime_selection(
        backend,
        native_production=True,
        health=gates,
        release_evidence=gates,
        audit=audit,
    )
    monkeypatch.setattr(facade.control_service, "_selection", selector)
    monkeypatch.setattr("agent.routes.workflow_control_security.get_workflow_backend_control_facade", lambda: facade)
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    token = generate_token({"sub": owner.subject, "tenant_id": owner.tenant_id, "role": "user"}, settings.secret_key)
    yield {
        "owner": owner,
        "ownership": ownership,
        "facade": facade,
        "bound": facade.bind(owner),
        "backend": backend,
        "gates": gates,
        "audit": audit,
        "selector": selector,
        "client": app.test_client(),
        "headers": {"Authorization": f"Bearer {token}"},
        "request": compile_request(xor_xml()),
    }
    ownership.clear()
    ownership.set_owner_resolver(None)


def test_ready_preflight_cannot_write_or_start(preflight_setup, monkeypatch):
    s = preflight_setup

    def forbidden(*args, **kwargs):
        pytest.fail("preflight attempted a mutation")

    monkeypatch.setattr(s["ownership"], "reserve", forbidden)
    monkeypatch.setattr(s["facade"].bindings, "put", forbidden)
    monkeypatch.setattr(s["backend"], "start_workflow", forbidden)
    result = s["bound"].preflight_workflow(s["request"])
    assert result["ready"] is True
    assert result["advisory"] is True
    assert result["runtime_id"] == "ananta-native"
    assert result["reason_codes"] == []
    assert result["definition_hash"] == s["request"].execution_graph["definition_hash"]
    assert result["plan_hash"] == workflow_start_plan(s["request"], tenant_id=s["owner"].tenant_id).plan_hash
    assert s["facade"].bindings.get(s["request"].workflow_id) is None
    assert s["ownership"]._owners == {}
    assert s["audit"].records == []
    assert s["gates"].plans[0].tenant_id == s["owner"].tenant_id


@pytest.mark.parametrize(
    "gate,reason",
    [
        ("health", "runtime_health_unavailable"),
        ("release", "runtime_release_evidence_unavailable"),
        ("capability", "runtime_capabilities_missing"),
    ],
)
def test_preflight_uses_actual_runtime_gates(preflight_setup, monkeypatch, gate, reason):
    s = preflight_setup
    if gate == "health":
        s["gates"].status = "unavailable"
    elif gate == "release":
        s["gates"].released = False
    else:
        monkeypatch.delenv("ANANTA_BPMN_EXECUTION_ENABLED")
        monkeypatch.setattr(
            s["facade"].control_service,
            "_selection",
            build_configured_workflow_runtime_selection(
                s["backend"],
                native_production=True,
                health=s["gates"],
                release_evidence=s["gates"],
                audit=s["audit"],
            ),
        )
    result = s["bound"].preflight_workflow(s["request"])
    assert result["ready"] is False
    assert reason in str(result["reason_codes"]) + str(result["rejected"])
    assert s["backend"].starts == 0
    assert s["audit"].records == []


def test_preview_and_start_selector_make_identical_decisions(preflight_setup):
    s = preflight_setup
    plan = workflow_start_plan(s["request"], tenant_id=s["owner"].tenant_id)
    args = dict(plan=plan, preferred_runtime="ananta-native", allowed_runtimes=("ananta-native",))
    preview = s["selector"].preview(**args)
    selected = s["selector"].select(**args)
    assert preview == replace(selected, audit_ref="")
    assert len(s["audit"].records) == 1


def test_start_revalidates_a_previously_ready_preflight(preflight_setup):
    s = preflight_setup
    assert s["bound"].preflight_workflow(s["request"])["ready"]
    s["gates"].released = False
    s["ownership"].reserve(s["request"].workflow_id, s["owner"])
    with pytest.raises(RuntimeError, match="workflow_runtime_selection_blocked"):
        s["bound"].start_workflow(s["request"])
    assert s["backend"].starts == 0


def test_foreign_workflow_is_not_reserved_or_disclosed(preflight_setup):
    s = preflight_setup
    s["ownership"].reserve(s["request"].workflow_id, WorkflowRoutePrincipal("other", "other"))
    result = s["bound"].preflight_workflow(s["request"])
    assert result["ready"] is False
    assert result["runtime_id"] is None
    assert result["reason_codes"] == ["workflow_preflight_denied"]
    assert s["gates"].plans == []


@pytest.mark.parametrize("graph_body", [True, False])
def test_authenticated_preflight_http_contract(preflight_setup, graph_body):
    s = preflight_setup
    body = (
        {"graph": import_bpmn_xml(xor_xml()).graph.model_dump(), "policy_scope": {"source": "synthetic-test"}}
        if graph_body
        else {"workflow_request": s["request"].to_dict()}
    )
    response = s["client"].post("/api/visual-process/workflow/preflight", json=body, headers=s["headers"])
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["ready"] is True
    assert {
        "ready",
        "advisory",
        "workflow_id",
        "definition_hash",
        "plan_hash",
        "runtime_id",
        "reason_codes",
        "required_capabilities",
        "rejected",
    } <= set(result)
    assert s["backend"].starts == 0
    assert s["ownership"]._owners == {}


def test_preflight_requires_auth_and_rejects_tampered_source(preflight_setup):
    s = preflight_setup
    url = "/api/visual-process/workflow/preflight"
    assert s["client"].post(url, json={}).status_code == 401
    raw = s["request"].to_dict()
    raw["execution_graph"]["definition_hash"] = "0" * 64
    response = s["client"].post(url, json={"workflow_request": raw}, headers=s["headers"])
    assert response.status_code == 422
    assert s["gates"].plans == []


@pytest.mark.parametrize("field", ["expected_plan_hash", "expected_definition_hash"])
def test_start_hash_mismatch_rejects_before_ownership_or_execution(preflight_setup, monkeypatch, field):
    s = preflight_setup

    def forbidden(*args, **kwargs):
        pytest.fail("stale start reserved ownership")

    monkeypatch.setattr(s["ownership"], "reserve", forbidden)
    response = s["client"].post(
        "/api/visual-process/workflow/start",
        headers=s["headers"],
        json={"workflow_request": s["request"].to_dict(), field: "0" * 64},
    )
    assert response.status_code == 409, response.get_json()
    assert s["backend"].starts == 0
    assert s["facade"].bindings.get(s["request"].workflow_id) is None


def test_ui_preflight_start_hashes_and_public_command_hints(preflight_setup, monkeypatch):
    s = preflight_setup
    original = s["backend"]._status
    monkeypatch.setattr(s["backend"], "_status", lambda *args: {**original(*args), "allowed_commands": ["cancel"]})
    body = {"workflow_request": s["request"].to_dict()}
    preview = (
        s["client"]
        .post(
            "/api/visual-process/workflow/preflight",
            json=body,
            headers=s["headers"],
        )
        .get_json()
    )
    started = s["client"].post(
        "/api/visual-process/workflow/start",
        headers=s["headers"],
        json={
            **body,
            "expected_plan_hash": preview["plan_hash"],
            "expected_definition_hash": preview["definition_hash"],
        },
    )
    assert started.status_code == 200, started.get_json()
    assert started.get_json()["definition_hash"] == preview["definition_hash"]
    assert started.get_json()["plan_hash"] == preview["plan_hash"]
    assert started.get_json()["allowed_commands"] == ["cancel"]


def test_public_definition_hash_is_bound_and_missing_command_hints_fail_closed(preflight_setup):
    from agent.services.workflow_transition_public_projection import canonical_workflow_public_status

    s = preflight_setup
    s["ownership"].reserve(s["request"].workflow_id, s["owner"])
    started = s["bound"].start_workflow(s["request"])
    assert started["allowed_commands"] == []
    binding = s["facade"].bindings.get(s["request"].workflow_id)
    raw = s["facade"].bindings.last_status(binding.workflow_id)
    with pytest.raises(ValueError, match="definition_hash_mismatch"):
        canonical_workflow_public_status(
            binding,
            {**raw, "definition_hash": "0" * 64},
            previous=None,
        )


def test_public_steps_use_canonical_activation_catalog_and_reject_unknown_ids(preflight_setup):
    from agent.services.workflow_backend import WorkflowStepRequest
    from agent.services.workflow_runtime_status_projection import _project_public_steps

    s = preflight_setup
    s["ownership"].reserve(s["request"].workflow_id, s["owner"])
    s["bound"].start_workflow(s["request"])
    binding = s["facade"].bindings.get(s["request"].workflow_id)
    # An expanded plan has a different node namespace from its source request.
    # This synthetic source catalog must never replace the saved plan catalog.
    binding = replace(binding, request=replace(binding.request, steps=(WorkflowStepRequest(step_id="source-region"),)))
    canonical_ids = [node["node_id"] for node in binding.execution_plan["nodes"]]
    observed = {"steps": [{"step_id": step_id, "status": "pending"} for step_id in canonical_ids]}
    projected = _project_public_steps(observed, binding=binding, allow_missing=False)
    assert [step["step_id"] for step in projected] == canonical_ids
    with pytest.raises(ValueError, match="source_step_unknown"):
        _project_public_steps(
            {"steps": [{"step_id": "source-region", "status": "completed"}]},
            binding=binding,
            allow_missing=False,
        )


@pytest.mark.parametrize("mode", ["shadow", "live", "missing", "foreign"])
def test_read_only_preflight_preserves_rollout_policy(preflight_setup, monkeypatch, mode):
    from agent.services.workflow_runtime_rollout_service import (
        InMemoryWorkflowRolloutPolicyStore,
        RolloutAwareRuntimeSelection,
        WorkflowRolloutAuditEvent,
        WorkflowRolloutPolicy,
        WorkflowRolloutPolicyService,
        WorkflowRolloutScope,
    )

    s = preflight_setup
    scope = WorkflowRolloutScope("project-preview")
    policies = WorkflowRolloutPolicyService(InMemoryWorkflowRolloutPolicyStore())
    if mode != "missing":
        # Seed an explicitly synthetic admitted policy snapshot. Promotion
        # authority itself is covered by the existing rollout security tests.
        policies.store.commit(
            WorkflowRolloutPolicy(
                scope=scope,
                policy_version="preview-policy",
                mode="shadow" if mode == "shadow" else "live",
                preferred_runtime="ananta-native",
                allowed_runtimes=("ananta-native",),
                required_capabilities=("audit", "authorization", "policy", "side_effect_guard"),
                allowed_side_effect_classes=("none", "read"),
            ),
            expected_revision=0,
            parent_revision=None,
            audit=WorkflowRolloutAuditEvent(
                "test-policy-1",
                scope,
                "synthetic_fixture",
                "test-operator",
                "synthetic-policy",
                1.0,
            ),
        )
    request = replace(
        s["request"],
        metadata={
            **s["request"].metadata,
            "workflow_rollout_scope": {
                "project_id": scope.project_id,
                "tenant_id": "foreign" if mode == "foreign" else s["owner"].tenant_id,
            },
        },
    )
    monkeypatch.setattr(
        s["facade"].control_service,
        "_selection",
        RolloutAwareRuntimeSelection(policies=policies, selection=s["selector"]),
    )
    before = policies.store.list_audit(scope)
    result = s["bound"].preflight_workflow(request)
    # Caller-supplied tenant metadata is rebound to the authenticated tenant by
    # the shared plan adapter, so a foreign hint cannot select foreign policy.
    assert result["ready"] is (mode in {"live", "foreign"}), result
    if mode not in {"live", "foreign"}:
        assert "workflow_rollout_" in str(result["reason_codes"])
    if mode == "foreign":
        assert s["gates"].plans[0].metadata["workflow_rollout_scope"]["tenant_id"] == s["owner"].tenant_id
    assert policies.store.list_audit(scope) == before
    assert s["audit"].records == []
    assert s["backend"].starts == 0
