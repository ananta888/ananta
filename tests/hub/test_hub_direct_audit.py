"""HDE-009: audit events, redaction and event order for hub-direct calls."""
import pytest

from agent.common import audit as audit_module
from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class OkRuntime:
    runtime_kind = "ok"

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "ok",
            "risk_class": "read",
            "evidence": [{"kind": "grep_match", "excerpt": "x" * 5000}],
            "warnings": [],
        }


@pytest.fixture
def captured_audit(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit_module, "log_audit", lambda action, details=None: events.append((action, details or {})))
    return events


def _cfg():
    return {
        "hub_direct_execution": {
            "enabled": True,
            "audit_enabled": True,
            "allowed_tools": ["repo.grep"],
        }
    }


def test_audit_constants_exist():
    assert audit_module.AUDIT_HUB_DIRECT_CANDIDATE_DETECTED == "hub_direct_candidate_detected"
    assert audit_module.AUDIT_HUB_DIRECT_TOOL_REQUESTED == "hub_direct_tool_requested"
    assert audit_module.AUDIT_HUB_DIRECT_TOOL_COMPLETED == "hub_direct_tool_completed"
    assert audit_module.AUDIT_HUB_DIRECT_TOOL_BLOCKED == "hub_direct_tool_blocked"
    assert audit_module.AUDIT_HUB_DIRECT_APPROVAL_REQUIRED == "hub_direct_approval_required"
    assert audit_module.AUDIT_HUB_DIRECT_FALLBACK_TO_WORKER == "hub_direct_fallback_to_worker"
    assert audit_module.AUDIT_WORKER_RUNTIME_DISPATCH == "worker_runtime_dispatch"


def test_successful_grep_emits_ordered_events(captured_audit, tmp_path):
    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(OkRuntime()))
    adapter.execute_direct(
        tool_name="repo.grep",
        arguments={"pattern": "x"},
        agent_cfg=_cfg(),
        task_id="task-9",
        workspace_ref=str(tmp_path),
    )
    actions = [action for action, _ in captured_audit]
    assert actions == [
        "hub_direct_tool_requested",
        "worker_runtime_dispatch",
        "hub_direct_tool_completed",
    ]
    for _, details in captured_audit:
        assert details.get("task_id") == "task-9"


def test_audit_payload_has_no_secrets_or_full_outputs(captured_audit, tmp_path):
    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(OkRuntime()))
    adapter.execute_direct(
        tool_name="repo.grep",
        arguments={"pattern": "x"},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    for action, details in captured_audit:
        serialized = str(details)
        assert "x" * 1000 not in serialized, f"full output leaked into {action}"
        assert "prompt" not in {key.lower() for key in details}


def test_blocked_call_emits_blocked_event(captured_audit, tmp_path):
    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(OkRuntime()))
    adapter.execute_direct(
        tool_name="repo.write_file",
        arguments={"path": "a", "content": "b", "mode": "create_only"},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    actions = [action for action, _ in captured_audit]
    assert "hub_direct_tool_blocked" in actions
    assert "worker_runtime_dispatch" not in actions


def test_audit_failure_does_not_break_execution(monkeypatch, tmp_path):
    def _boom(action, details=None):
        raise RuntimeError("audit kaputt")

    monkeypatch.setattr(audit_module, "log_audit", _boom)
    adapter = HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(OkRuntime()))
    result = adapter.execute_direct(
        tool_name="repo.grep",
        arguments={"pattern": "x"},
        agent_cfg=_cfg(),
        workspace_ref=str(tmp_path),
    )
    assert result["kind"] == "direct_tool_result"
    assert result["tool_result"]["status"] == "ok"
