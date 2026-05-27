from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.common.errors import ToolGuardrailError
from agent.models import TaskExecutionPolicyContract
from agent.services.execution_risk_policy_service import ExecutionRiskDecision
from agent.services.task_execution_service import TaskExecutionService


def _policy() -> TaskExecutionPolicyContract:
    return TaskExecutionPolicyContract(timeout_seconds=10, retries=0, retry_delay_seconds=0, source="test")


def _allow_risk(*, command, tool_calls, task, agent_cfg, command_analysis=None):
    return ExecutionRiskDecision(True, False, "low", [], [], {})


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def emit(self, *, operation_type, outcome, **kwargs):
        self.events.append((str(operation_type), str(outcome)))


def test_execute_local_step_blocks_unknown_high_risk_mutation_in_strict_mode() -> None:
    svc = TaskExecutionService()
    audit = _AuditCollector()
    with (
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
        patch("agent.services.task_execution_service.get_execution_audit_service", return_value=audit),
    ):
        with pytest.raises(ToolGuardrailError):
            svc.execute_local_step(
                tid=None,
                task={"worker_execution_context": {}},
                command=None,
                tool_calls=[{"name": "artifact_upload", "args": {"artifact_id": "a-1"}}],
                execution_policy=_policy(),
                guard_cfg={
                    "governance_mode": "strict",
                    "execution_risk_policy": {"enabled": True, "task_scoped_only": False},
                },
            )
    assert any(event[0] == "mutation_gate_decision" and event[1] == "blocked" for event in audit.events)


def test_execute_local_step_blocks_when_global_deny_switch_enabled() -> None:
    svc = TaskExecutionService()
    audit = _AuditCollector()
    with (
        patch("agent.services.task_execution_service.evaluate_execution_risk", side_effect=_allow_risk),
        patch("agent.services.task_execution_service.get_execution_audit_service", return_value=audit),
    ):
        with pytest.raises(ToolGuardrailError):
            svc.execute_local_step(
                tid=None,
                task={"worker_execution_context": {}},
                command="chmod +x scripts/run.sh",
                tool_calls=None,
                execution_policy=_policy(),
                guard_cfg={
                    "governance_mode": "balanced",
                    "mutation_gate": {"enabled": True, "global_deny_mutations": True},
                    "execution_risk_policy": {"enabled": True, "task_scoped_only": False},
                },
            )
    assert any(event[0] == "mutation_gate_decision" and event[1] == "blocked" for event in audit.events)
