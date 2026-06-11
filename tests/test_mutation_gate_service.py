from __future__ import annotations

import time

from agent.services.approval_policy_service import ApprovalDecision
from agent.services.execution_risk_policy_service import ExecutionRiskDecision
from agent.services.mutation_gate_service import get_mutation_gate_service


def _approval(*, classification: str = "allow", reason_code: str = "ok", operation_class: str = "mutation") -> ApprovalDecision:
    return ApprovalDecision(
        classification=classification,
        reason_code=reason_code,
        required_confirmation_level="operator" if classification == "confirm_required" else "none",
        operation_class=operation_class,
        governance_mode="strict",
        enforced=True,
        policy_source="test",
        details={},
    )


def _risk(*, allowed: bool = True, risk_level: str = "low") -> ExecutionRiskDecision:
    return ExecutionRiskDecision(
        allowed=allowed,
        review_required=False,
        risk_level=risk_level,
        reasons=[] if allowed else [f"execution_risk_denied:{risk_level}"],
        blocked_tools=[],
        details={},
    )


def test_mutation_target_normalization_produces_stable_fingerprint() -> None:
    svc = get_mutation_gate_service()
    target = svc.normalize_target(
        command="chmod +x ./scripts/run.sh",
        tool_calls=None,
        task={"id": "task-1", "goal_id": "goal-1"},
    )
    assert target["target_type"] == "path"
    assert target["path"].endswith("/scripts/run.sh")
    assert len(str(target["target_fingerprint"])) == 64


def test_mutation_gate_blocks_when_approval_policy_blocks() -> None:
    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-2"},
        agent_cfg={},
        approval_decision=_approval(classification="blocked", reason_code="approval_blocked:mutation"),
        risk_decision=_risk(),
        trace_id="trace-1",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "approval_blocked:mutation"


def test_mutation_gate_blocks_when_risk_policy_denies() -> None:
    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command="rm -rf /tmp/demo",
        tool_calls=None,
        task={"id": "task-3"},
        agent_cfg={},
        approval_decision=_approval(),
        risk_decision=_risk(allowed=False, risk_level="critical"),
        trace_id="trace-2",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"].startswith("execution_risk_denied")


def test_mutation_gate_requires_confirmation_without_scoped_approval() -> None:
    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-4", "approval_confirmed": False},
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-3",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "confirm_required"
    assert payload["reason_code"] == "mutation_scope_confirmation_required"


def test_mutation_gate_allows_scoped_approval_binding() -> None:
    svc = get_mutation_gate_service()
    task = {"id": "task-5", "goal_id": "goal-5"}
    target = svc.normalize_target(command="chmod +x scripts/run.sh", tool_calls=None, task=task)
    task["mutation_approval"] = {
        "task_id": "task-5",
        "trace_id": "trace-5",
        "mutation_classes": ["file_write"],
        "target_fingerprint": target["target_fingerprint"],
        "expires_at": time.time() + 300,
    }
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-5",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "allow"
    assert payload["reason_code"] == "mutation_scope_approved"


def test_mutation_gate_fails_closed_for_expired_scoped_approval() -> None:
    svc = get_mutation_gate_service()
    task = {"id": "task-6", "goal_id": "goal-6", "approval_confirmed": True}
    target = svc.normalize_target(command="chmod +x scripts/run.sh", tool_calls=None, task=task)
    task["mutation_approval"] = {
        "task_id": "task-6",
        "trace_id": "trace-6",
        "mutation_classes": ["file_write"],
        "target_fingerprint": target["target_fingerprint"],
        "expires_at": time.time() - 1,
    }
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-6",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "mutation_scope_expired"


def test_mutation_gate_fails_closed_for_unknown_mutation_in_strict_mode() -> None:
    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command=None,
        tool_calls=[{"name": "artifact_upload", "args": {"artifact_id": "a-1"}}],
        task={"id": "task-7"},
        agent_cfg={"governance_mode": "strict"},
        approval_decision=_approval(classification="allow", operation_class="read_only"),
        risk_decision=_risk(),
        trace_id="trace-7",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "mutation_gate_unknown_high_risk_classification"


def test_mutation_gate_blocks_when_scoped_target_mismatches() -> None:
    svc = get_mutation_gate_service()
    task = {"id": "task-8", "goal_id": "goal-8"}
    target = svc.normalize_target(command="chmod +x scripts/run.sh", tool_calls=None, task=task)
    task["mutation_approval"] = {
        "task_id": "task-8",
        "trace_id": "trace-8",
        "mutation_classes": ["file_write"],
        "target_fingerprint": "deadbeef",
        "expires_at": time.time() + 300,
    }
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-8",
    )
    payload = decision.as_dict()
    assert target["target_fingerprint"] != "deadbeef"
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "mutation_scope_mismatch:target"


def test_mutation_gate_blocks_when_scoped_class_mismatches() -> None:
    svc = get_mutation_gate_service()
    task = {"id": "task-9", "goal_id": "goal-9"}
    target = svc.normalize_target(command="chmod +x scripts/run.sh", tool_calls=None, task=task)
    task["mutation_approval"] = {
        "task_id": "task-9",
        "trace_id": "trace-9",
        "mutation_classes": ["artifact_mutation"],
        "target_fingerprint": target["target_fingerprint"],
        "expires_at": time.time() + 300,
    }
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-9",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "mutation_scope_mismatch:class"


def test_mutation_gate_global_deny_switch_blocks_mutation() -> None:
    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-10"},
        agent_cfg={"mutation_gate": {"enabled": True, "global_deny_mutations": True}},
        approval_decision=_approval(classification="allow"),
        risk_decision=_risk(),
        trace_id="trace-10",
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["reason_code"] == "mutation_gate_global_deny"


def test_mutation_target_normalization_handles_evolver_target_refs() -> None:
    svc = get_mutation_gate_service()
    task = {"id": "task-11", "goal_id": "goal-11", "working_directory": "/home/krusty/ananta"}
    call_a = [
        {
            "name": "evolution_apply",
            "args": {"target_refs": [{"path": "./agent/routes/evolution.py", "type": "file"}]},
        }
    ]
    call_b = [
        {
            "name": "evolution_apply",
            "args": {"target_refs": [{"path": "agent/routes/evolution.py", "type": "file"}]},
        }
    ]
    normalized_a = svc.normalize_target(command=None, tool_calls=call_a, task=task)
    normalized_b = svc.normalize_target(command=None, tool_calls=call_b, task=task)
    assert normalized_a["target_type"] == "path"
    assert normalized_a["path"].endswith("/agent/routes/evolution.py")
    assert normalized_a["target_refs_digest"]
    assert normalized_a["target_fingerprint"] == normalized_b["target_fingerprint"]


# ALWA-006: arguments_digest-based scoped-approval binding. A scoped
# approval may pin the exact call via arguments_digest; a mismatch must
# not silently allow.
def test_mutation_scope_arguments_digest_mismatch_blocks() -> None:
    svc = get_mutation_gate_service()
    target = svc.normalize_target(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-d1", "goal_id": "goal-d1"},
    )
    task = {
        "id": "task-d1",
        "goal_id": "goal-d1",
        "mutation_approval": {
            "task_id": "task-d1",
            "trace_id": "trace-d1",
            "actor": "operator",
            "mutation_classes": ["shell"],
            "expires_at": time.time() + 600,
            "target_fingerprint": target["target_fingerprint"],
            "arguments_digest": "deadbeef" * 8,
        },
    }
    result = svc._validate_scoped_approval(
        task=task,
        mutation_class="shell",
        normalized_target=target,
        trace_id="trace-d1",
        actor="operator",
        arguments_digest="cafebabe" * 8,
    )
    assert result["ok"] is False
    assert result["reason_code"] == "mutation_scope_mismatch:arguments_digest"


def test_mutation_scope_arguments_digest_match_allows() -> None:
    svc = get_mutation_gate_service()
    target = svc.normalize_target(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-d2", "goal_id": "goal-d2"},
    )
    arguments_digest = svc._compute_call_digest(
        call_arguments={"command": "chmod +x scripts/run.sh"},
        target_fingerprint=target["target_fingerprint"],
    )
    task = {
        "id": "task-d2",
        "goal_id": "goal-d2",
        "mutation_approval": {
            "task_id": "task-d2",
            "trace_id": "trace-d2",
            "actor": "operator",
            "mutation_classes": ["shell"],
            "expires_at": time.time() + 600,
            "target_fingerprint": target["target_fingerprint"],
            "arguments_digest": arguments_digest,
        },
    }
    result = svc._validate_scoped_approval(
        task=task,
        mutation_class="shell",
        normalized_target=target,
        trace_id="trace-d2",
        actor="operator",
        arguments_digest=arguments_digest,
    )
    assert result["ok"] is True
    assert result["reason_code"] == "mutation_scope_ok"


def test_mutation_scope_arguments_digest_omitted_skips_check() -> None:
    """ALWA-006: an empty digest in the scope must not block; it just
    skips the digest check (legacy scope without pinning is still
    honored for the other dimensions)."""
    svc = get_mutation_gate_service()
    target = svc.normalize_target(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-d3", "goal_id": "goal-d3"},
    )
    task = {
        "id": "task-d3",
        "goal_id": "goal-d3",
        "mutation_approval": {
            "task_id": "task-d3",
            "trace_id": "trace-d3",
            "actor": "operator",
            "mutation_classes": ["shell"],
            "expires_at": time.time() + 600,
            "target_fingerprint": target["target_fingerprint"],
            # arguments_digest intentionally absent
        },
    }
    result = svc._validate_scoped_approval(
        task=task,
        mutation_class="shell",
        normalized_target=target,
        trace_id="trace-d3",
        actor="operator",
        arguments_digest="anything",
    )
    assert result["ok"] is True
    assert result["reason_code"] == "mutation_scope_ok"
