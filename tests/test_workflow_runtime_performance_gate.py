from __future__ import annotations

import json
import runpy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.workflow_runtime_rollout import workflow_runtime_rollout_bp
from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime_performance_gate import (
    COMPOSE_REFERENCE_PROFILE,
    WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA,
    ComposeWorkflowRuntimePerformanceEvidence,
    JsonWorkflowRolloutPerformanceEvidenceStore,
    nearest_rank_percentile,
)
from agent.services.workflow_runtime_rollout_service import (
    InMemoryWorkflowRolloutPolicyStore,
    WorkflowRolloutPolicy,
    WorkflowRolloutPolicyService,
    WorkflowRolloutScope,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run-workflow-runtime-performance-gate.py"


def test_performance_gate_is_directly_executable_from_repository_root() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "sample-projection" in completed.stdout


def _samples(value: float = 10.0) -> dict[str, list[float]]:
    return {
        "start": [value] * 10,
        "signal": [value] * 10,
        "event_projection": [value] * 10,
        "worker_restart_resume": [value],
    }


def _component(component: str, metrics: dict[str, list[float]]) -> dict:
    return {
        "schema": WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA,
        "component": component,
        "runtime_id": "temporal",
        "reference_profile": COMPOSE_REFERENCE_PROFILE,
        "metrics": {metric: {"samples_ms": samples} for metric, samples in metrics.items()},
    }


def test_nearest_rank_p95_is_computed_from_raw_samples() -> None:
    values = [float(value) for value in range(1, 21)]

    assert nearest_rank_percentile(values, 95) == 19.0


def test_compose_evidence_round_trips_and_binds_rollout_metrics() -> None:
    evidence = ComposeWorkflowRuntimePerformanceEvidence.build(
        runtime_id="temporal",
        source_revision="revision-a",
        generated_at=1_700_000_000.0,
        samples=_samples(),
    )

    restored = ComposeWorkflowRuntimePerformanceEvidence.from_mapping(evidence.to_dict())
    rollout = restored.to_rollout_evidence()

    assert restored.evidence_id.startswith("wrpe-")
    assert rollout.evidence_ref == restored.evidence_id
    assert rollout.start_p95_ms == 10.0
    assert rollout.worker_restart_resume_p95_ms == 10.0
    rollout.assert_promotion_safe()


@pytest.mark.parametrize(
    ("metric", "threshold"),
    [
        ("start", 2_000.0),
        ("signal", 2_000.0),
        ("event_projection", 1_000.0),
        ("worker_restart_resume", 30_000.0),
    ],
)
def test_equal_threshold_is_release_blocking(metric: str, threshold: float) -> None:
    samples = _samples()
    samples[metric] = [threshold] * len(samples[metric])

    with pytest.raises(
        RuntimeError,
        match=f"workflow_runtime_performance_p95_exceeded:{metric}",
    ):
        ComposeWorkflowRuntimePerformanceEvidence.build(
            runtime_id="temporal",
            source_revision="revision-a",
            generated_at=1_700_000_000.0,
            samples=samples,
        )


def test_tampered_summary_and_stale_revision_fail_closed(tmp_path: Path) -> None:
    evidence = ComposeWorkflowRuntimePerformanceEvidence.build(
        runtime_id="temporal",
        source_revision="revision-a",
        generated_at=1_700_000_000.0,
        samples=_samples(),
    )
    raw = evidence.to_dict()
    raw["metrics"]["start"]["p95_ms"] = 0.1
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="summary_mismatch"):
        JsonWorkflowRolloutPerformanceEvidenceStore(path).get_evidence(
            scope=WorkflowRolloutScope("project-a"),
            runtime_id="temporal",
        )

    path.write_text(json.dumps(evidence.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="revision_mismatch"):
        JsonWorkflowRolloutPerformanceEvidenceStore(
            path,
            expected_source_revision="revision-b",
        ).get_evidence(
            scope=WorkflowRolloutScope("project-a"),
            runtime_id="temporal",
        )


def test_projection_sampler_and_component_evaluator_emit_machine_evidence(
    tmp_path: Path,
) -> None:
    script = runpy.run_path(str(SCRIPT))
    projection = script["collect_projection_samples"](10)
    temporal = _component(
        "temporal_start_signal",
        {"start": [20.0] * 10, "signal": [30.0] * 10},
    )
    restart = _component(
        "worker_restart_resume",
        {"worker_restart_resume": [500.0]},
    )
    paths = []
    for name, value in (
        ("temporal.jsonl", temporal),
        ("projection.json", projection),
        ("restart.json", restart),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        paths.append(path)

    evidence = script["evaluate_components"](
        paths,
        source_revision="revision-compose",
        generated_at=1_700_000_001.0,
    )

    assert evidence.status == "passed"
    assert set(evidence.to_dict()["metrics"]) == {
        "start",
        "signal",
        "event_projection",
        "worker_restart_resume",
    }
    assert evidence.metric("event_projection").p95_ms < 1_000.0


def _live_policy() -> WorkflowRolloutPolicy:
    return WorkflowRolloutPolicy(
        scope=WorkflowRolloutScope("project-a", "tenant-a"),
        policy_version="rollout-v1",
        mode="live",
        preferred_runtime="temporal",
        allowed_runtimes=("temporal",),
        required_capabilities=("audit", "authorization"),
    )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-v1",
        nodes=(ExecutionNode(node_id="step-a"),),
        capabilities=("audit", "authorization"),
        metadata={
            "workflow_rollout_scope": {
                "project_id": "project-a",
                "tenant_id": "tenant-a",
            }
        },
    )


def test_direct_live_policy_write_cannot_forge_promotion_action() -> None:
    policies = WorkflowRolloutPolicyService(InMemoryWorkflowRolloutPolicyStore())

    with pytest.raises(ValueError, match="live_requires_admission_service"):
        policies.set_policy(
            _live_policy(),
            expected_revision=0,
            actor_id="operator-a",
            reason_code="attempted-bypass",
            change_id="change-a",
            action="performance_safe_promotion",
        )


def test_admin_route_exposes_only_evidence_gated_promotion(monkeypatch) -> None:
    result = SimpleNamespace(
        stored_policy=SimpleNamespace(policy=_live_policy(), revision=1),
        runtime_selection=RuntimeSelection(
            runtime_id="temporal",
            capabilities=frozenset({"audit", "authorization"}),
            mode="durable",
            reason_code="runtime_selected_preferred",
            audit_ref="selection-audit-a",
        ),
        performance_evidence=SimpleNamespace(
            evidence_ref="wrpe-evidence-a",
            source_revision="revision-a",
        ),
        shadow_comparison_evidence=SimpleNamespace(
            evidence_ref="wsc-evidence-a",
        ),
    )
    calls: list[dict] = []
    service = SimpleNamespace(promote=lambda **kwargs: calls.append(kwargs) or result)
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_rollout.get_workflow_runtime_promotion_service",
        lambda: service,
    )
    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_rollout.log_audit",
        lambda action, details: audits.append((action, details)),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(workflow_runtime_rollout_bp)
    client = app.test_client()

    def headers(role: str) -> dict[str, str]:
        token = generate_token(
            {"sub": "operator-a", "tenant_id": "tenant-a", "role": role},
            settings.secret_key,
        )
        return {"Authorization": f"Bearer {token}"}

    payload = {
        "policy": _live_policy().to_dict(),
        "plan": _plan().to_dict(),
        "expected_revision": 0,
        "reason_code": "compose-evidence-green",
        "change_id": "promotion-a",
        "approval_id": "approval-a",
    }
    assert (
        client.post(
            "/api/workflow-runtime/rollout/promotions",
            json=payload,
            headers=headers("user"),
        ).status_code
        == 403
    )

    response = client.post(
        "/api/workflow-runtime/rollout/promotions",
        json=payload,
        headers=headers("admin"),
    )
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["promotion"]["performance_evidence_ref"] == ("wrpe-evidence-a")
    assert response.get_json()["promotion"]["shadow_comparison_ref"] == ("wsc-evidence-a")
    assert len(calls) == 1
    assert calls[0]["approval_id"] == "approval-a"
    assert len(audits) == 1
    assert audits[0][0] == "workflow_runtime_performance_promotion"
    assert audits[0][1]["approval_id"] == "approval-a"
    assert audits[0][1]["tenant_id"] == "tenant-a"


def test_promotion_route_rejects_cross_tenant_plan_before_service(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_rollout.get_workflow_runtime_promotion_service",
        lambda: (_ for _ in ()).throw(AssertionError("service must not be called")),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(workflow_runtime_rollout_bp)
    token = generate_token(
        {"sub": "operator-a", "tenant_id": "tenant-a", "role": "admin"},
        settings.secret_key,
    )
    foreign_plan = replace(_plan(), tenant_id="tenant-b").to_dict()

    response = app.test_client().post(
        "/api/workflow-runtime/rollout/promotions",
        json={
            "policy": _live_policy().to_dict(),
            "plan": foreign_plan,
            "expected_revision": 0,
            "reason_code": "should-be-denied",
            "change_id": "promotion-foreign",
            "approval_id": "approval-foreign",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.get_json()["reason_code"] == ("workflow_rollout_promotion_tenant_mismatch")


def test_tenant_admin_cannot_promote_project_wide_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_rollout.get_workflow_runtime_promotion_service",
        lambda: (_ for _ in ()).throw(AssertionError("service must not be called")),
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(workflow_runtime_rollout_bp)
    token = generate_token(
        {"sub": "operator-a", "tenant_id": "tenant-a", "role": "admin"},
        settings.secret_key,
    )

    response = app.test_client().post(
        "/api/workflow-runtime/rollout/promotions",
        json={
            "policy": replace(_live_policy(), scope=WorkflowRolloutScope("project-a")).to_dict(),
            "plan": _plan().to_dict(),
            "expected_revision": 1,
            "reason_code": "must-be-global",
            "change_id": "promotion-project",
            "approval_id": "approval-project",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["reason_code"] == ("workflow_rollout_project_scope_global_admin_required")
