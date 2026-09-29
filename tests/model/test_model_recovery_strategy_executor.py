from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.model_recovery_strategy_executor import (
    ModelRecoveryStrategyExecutor,
)


class _PlanningPort:
    def __init__(
        self,
        *,
        actions: list[str],
        approval_required: bool = True,
        signal_valid: bool = True,
    ) -> None:
        self.actions = list(actions)
        self.approval_required = approval_required
        self.signal_valid = signal_valid
        self.compaction_calls = 0
        self.plan_calls = 0

    def resolve_recovery_policy(self, _task):
        return self.actions, self.approval_required, "policy-hash"

    def summarize_exhaustion_signal(self, _failures):
        return {"terminal": True} if self.signal_valid else None

    def compact_context_for_recovery(self, _task, *, actions):
        self.compaction_calls += 1
        assert actions == self.actions
        return "bounded compacted context", {
            "status": "fallback",
            "input_chars": 50_000,
            "output_chars": 5120,
            "reduction_ratio": 0.1024,
            "provider": "must-not-be-persisted",
        }

    def propose_after_model_exhaustion(self, **_values):
        self.plan_calls += 1
        return {
            "status": "pending_approval",
            "reason_code": "recovery_plan_pending_approval",
            "plan_id": "plan-1",
            "approval_request_id": "approval-1",
        }


def _execute(port: _PlanningPort) -> dict:
    return ModelRecoveryStrategyExecutor(
        role_provider=lambda: "hub",
        planning_port_provider=lambda: port,
    ).execute_after_model_exhaustion(
        task=SimpleNamespace(id="task-1"),
        strategy_failures=[{"failure_type": "invalid_proposal"}],
    )


@pytest.mark.parametrize(
    ("actions", "reason_code"),
    [
        ([], "model_recovery_disabled"),
        (["stop"], "model_recovery_stop_selected"),
        (
            ["require_approval", "stop"],
            "model_recovery_stop_selected",
        ),
    ],
)
def test_non_planning_strategies_stop_without_planner_or_provider_calls(
    actions: list[str],
    reason_code: str,
) -> None:
    port = _PlanningPort(actions=actions)

    outcome = _execute(port)

    assert outcome["status"] == "stopped"
    assert outcome["reason_code"] == reason_code
    assert outcome["terminal_model_chain_handled"] is True
    assert port.compaction_calls == 0
    assert port.plan_calls == 0


def test_compact_then_stop_is_bounded_and_does_not_persist_raw_context() -> None:
    port = _PlanningPort(actions=["compact_context", "stop"])

    outcome = _execute(port)

    assert outcome["status"] == "stopped"
    assert outcome["reason_code"] == "model_recovery_stop_selected"
    assert outcome["compaction"] == {
        "status": "fallback",
        "input_chars": 50_000,
        "output_chars": 5120,
        "reduction_ratio": 0.1024,
    }
    assert len(outcome["compacted_context_hash"]) == 64
    assert "bounded compacted context" not in str(outcome)
    assert port.compaction_calls == 1
    assert port.plan_calls == 0


@pytest.mark.parametrize(
    "actions",
    [
        ["segment_planning", "require_approval", "stop"],
        ["propose_task_plan", "require_approval", "stop"],
        [
            "compact_context",
            "segment_planning",
            "propose_task_plan",
            "require_approval",
            "stop",
        ],
    ],
)
def test_plan_strategies_delegate_once_to_approval_gated_hub_saga(
    actions: list[str],
) -> None:
    port = _PlanningPort(actions=actions)

    outcome = _execute(port)

    assert outcome["status"] == "pending_approval"
    assert outcome["plan_id"] == "plan-1"
    assert outcome["terminal_model_chain_handled"] is True
    assert outcome["recovery_actions"] == actions
    assert port.plan_calls == 1
    assert port.compaction_calls == 0


def test_plan_strategy_without_exact_approval_stops_fail_closed() -> None:
    port = _PlanningPort(
        actions=["segment_planning", "require_approval", "stop"],
        approval_required=False,
    )

    outcome = _execute(port)

    assert outcome["status"] == "stopped"
    assert outcome["reason_code"] == "recovery_plan_approval_required"
    assert port.plan_calls == 0


def test_unverified_exhaustion_signal_is_not_handled_as_recovery() -> None:
    port = _PlanningPort(actions=["stop"], signal_valid=False)

    outcome = _execute(port)

    assert outcome == {
        "status": "ignored",
        "reason_code": "model_exhaustion_signal_required",
        "terminal_model_chain_handled": False,
    }
    assert port.compaction_calls == 0
    assert port.plan_calls == 0
