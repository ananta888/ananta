from __future__ import annotations

import ast
import json
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime._operations_rollout_support import (
    drill_runtime_candidates,
    rollout_drill_plan,
)
from agent.services.workflow_runtime.operations_drills import (
    AuthorizationKeyRotationDrill,
    OperationsDrillResult,
    RolloutLifecycleDrill,
    WorkflowRuntimeOperationsDrillReport,
)
from agent.services.workflow_runtime_rollout_service import (
    InMemoryWorkflowRolloutPolicyStore,
    WorkflowRolloutPolicyService,
    WorkflowRolloutScope,
    WorkflowRuntimeRollbackService,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-workflow-runtime-operations-drills.py"


def test_operations_drill_script_runs_all_release_blocking_drills(tmp_path: Path) -> None:
    workspace = tmp_path / "drill"
    output = tmp_path / "evidence.json"
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(workspace),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["source_revision"].startswith(
        source_revision + "+dirty.sha256."
    )
    assert len(payload["source_revision"].rsplit(".", 1)[-1]) == 64
    assert {result["drill_id"] for result in payload["results"]} == {
        "alembic-upgrade-downgrade-n-minus-one",
        "authorization-key-rotation",
        "database-backup-restore",
        "incident-containment-recovery",
        "rollout-lifecycle-promotion-rollback",
    }
    assert all(result["status"] == "passed" for result in payload["results"])
    rollout = next(
        result for result in payload["results"] if result["drill_id"] == "rollout-lifecycle-promotion-rollback"
    )
    assert rollout["evidence"]["policy_modes"] == [
        "disabled",
        "shadow",
        "live",
        "live",
    ]
    assert rollout["evidence"]["shadow_suppressed_intents"] == 2
    assert rollout["evidence"]["shadow_comparison_status"] == "passed"
    assert rollout["evidence"]["shadow_comparison_ref"].startswith("wsc-")
    assert rollout["evidence"]["approval_gate_rejection_observed"] is True
    assert rollout["evidence"]["evidence_gate_rejection_observed"] is True
    assert rollout["evidence"]["shadow_drift_rejection_observed"] is True
    assert rollout["evidence"]["rollback_runtime"] == "langgraph"
    assert payload["evidence_id"].startswith("wrod-")
    assert str(tmp_path) not in completed.stdout
    assert "disposable-air055" not in completed.stdout
    backup = workspace / "workflow-runtime.backup.sqlite"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_operations_drill_script_allocates_automatic_workspace(tmp_path: Path) -> None:
    output = tmp_path / "automatic-evidence.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "passed"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"


def test_operations_report_is_content_addressed_and_tamper_evident() -> None:
    first = WorkflowRuntimeOperationsDrillReport(
        source_revision="revision-a",
        results=(OperationsDrillResult("drill-a", ("invariant-a",), {"count": 1}),),
    )
    same = WorkflowRuntimeOperationsDrillReport(
        source_revision="revision-a",
        results=(OperationsDrillResult("drill-a", ("invariant-a",), {"count": 1}),),
    )
    tampered = WorkflowRuntimeOperationsDrillReport(
        source_revision="revision-a",
        results=(OperationsDrillResult("drill-a", ("invariant-a",), {"count": 2}),),
    )

    assert first.to_dict() == same.to_dict()
    assert first.evidence_id != tampered.evidence_id


def test_authorization_key_rotation_drill_emits_no_key_material() -> None:
    result = AuthorizationKeyRotationDrill().run().to_dict()

    assert result["status"] == "passed"
    assert result["evidence"]["active_key_id"] == "rotation-new"
    assert result["evidence"]["secret_material_emitted"] is False
    assert "signature" not in json.dumps(result)


def test_rollout_drill_rejects_rollback_target_with_capability_loss() -> None:
    candidates = tuple(
        replace(candidate, capabilities=candidate.capabilities - {"resume"})
        if candidate.runtime_id == "langgraph"
        else candidate
        for candidate in drill_runtime_candidates()
    )
    policies = WorkflowRolloutPolicyService(
        InMemoryWorkflowRolloutPolicyStore(),
        clock=lambda: 400.0,
    )

    with pytest.raises(RuntimeError, match="rollback_target_not_safe"):
        RolloutLifecycleDrill(policies, candidates=candidates).run(source_revision="revision-a")

    stored = policies.store.get(WorkflowRolloutScope("project-rollout-drill"))
    assert stored is not None
    assert stored.revision == 3
    assert stored.policy.preferred_runtime == "ananta-native"
    assert "capability_safe_rollback" not in {
        event.action for event in policies.store.list_audit(WorkflowRolloutScope("project-rollout-drill"))
    }


def test_rollback_defense_rejects_selector_capability_misreport() -> None:
    policies = WorkflowRolloutPolicyService(
        InMemoryWorkflowRolloutPolicyStore(),
        clock=lambda: 400.0,
    )
    RolloutLifecycleDrill(policies).run(source_revision="revision-a")

    class CapabilityLosingSelection:
        def select(self, **_: object) -> RuntimeSelection:
            return RuntimeSelection(
                runtime_id="ananta-native",
                capabilities=frozenset({"audit", "authorization", "policy"}),
                mode="live",
                reason_code="runtime_selected_preferred",
                audit_ref="selection-capability-loss",
            )

    with pytest.raises(RuntimeError, match="rollback_capability_loss"):
        WorkflowRuntimeRollbackService(
            policies=policies,
            selection=CapabilityLosingSelection(),
        ).rollback(
            scope=WorkflowRolloutScope("project-rollout-drill"),
            plan=rollout_drill_plan(),
            target_runtime="ananta-native",
            policy_version="unsafe-rollback-v1",
            expected_revision=4,
            actor_id="hub-release-controller",
            reason_code="defense_in_depth_test",
            change_id="unsafe-rollback-change",
        )

    stored = policies.store.get(WorkflowRolloutScope("project-rollout-drill"))
    assert stored is not None
    assert stored.revision == 4
    assert stored.policy.preferred_runtime == "langgraph"


def test_operations_drill_command_fails_closed_without_path_disclosure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "already-exists"
    workspace.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(workspace),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload == {
        "reason_code": "workflow_operations_drill_failed",
        "schema": "ananta.workflow_runtime_operations_drill.v1",
        "status": "failed",
    }
    assert str(tmp_path) not in completed.stdout


def test_operations_drill_rejects_stale_source_revision(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(tmp_path / "drill"),
            "--source-revision",
            "stale-source-revision",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["reason_code"] == ("workflow_operations_source_revision_mismatch")


def test_air055_runbook_and_ci_execute_the_drill_command() -> None:
    command = "python scripts/run-workflow-runtime-operations-drills.py"
    rollout = (ROOT / "docs/operations/workflow-runtime-rollout.md").read_text(encoding="utf-8")
    temporal = (ROOT / "docs/operations/temporal-runtime.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/quality-and-docs.yml").read_text(encoding="utf-8")

    assert command in rollout
    assert command in temporal
    assert "run-workflow-runtime-operations-drills.py" in workflow


def test_operations_drills_preserve_hub_worker_control_boundary() -> None:
    source = (ROOT / "agent/services/workflow_runtime/operations_drills.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} | {
        str(node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert not any(name == "worker" or name.startswith("worker.") for name in imported)
    assert "WorkflowRolloutPolicyService" in source
    assert "SQLAlchemyWorkflowAuthorizationGrantService" in source
