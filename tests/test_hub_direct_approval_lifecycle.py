"""HDE-008: digest-bound approval lifecycle for hub-direct execution.

In-memory SQLite world (pattern from test_alwa_e2e_pipeline): the
production approval service runs unchanged against a fresh engine.
"""
from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter


class CountingRuntime:
    runtime_kind = "counting"

    def __init__(self):
        self.calls = []

    def execute_tool(self, *, tool_name, arguments, workspace_dir, tool_call_id, config):
        self.calls.append({"tool_name": tool_name, "arguments": dict(arguments)})
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "ok",
            "risk_class": "write",
            "evidence": [],
            "warnings": [],
        }


@pytest.fixture
def approval_world(monkeypatch, tmp_path):
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr("agent.services.approval_request_service._engine", lambda: test_engine)
    monkeypatch.setattr(
        "agent.services.approval_request_service.ApprovalRequestService._payload_dir",
        staticmethod(lambda: tmp_path / "payloads"),
    )
    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr("agent.common.audit.log_audit", lambda action, details=None: audit_events.append((action, details or {})))
    from agent.services.approval_request_service import get_approval_request_service

    return {"svc": get_approval_request_service(), "audit": audit_events}


def _cfg():
    return {
        "hub_direct_execution": {
            "enabled": True,
            "audit_enabled": True,
            "allowed_tools": ["git.add_selected", "repo.grep"],
        },
        "approval_lifecycle": {"enabled": True},
    }


def _adapter(runtime):
    return HubToolExecutionAdapter(runtime_adapter=WorkerRuntimeExecutionAdapter(runtime))


_CALL_ARGS = {"paths": ["a.txt"]}


def _request_direct(adapter, tmp_path, arguments=_CALL_ARGS):
    return adapter.execute_direct(
        tool_name="git.add_selected",
        arguments=arguments,
        agent_cfg=_cfg(),
        task_id="task-1",
        workspace_ref=str(tmp_path),
        mutation_mode="controlled_workspace",
    )


def test_pending_approval_request_is_created_with_hub_direct_scope(approval_world, tmp_path):
    runtime = CountingRuntime()
    result = _request_direct(_adapter(runtime), tmp_path)
    assert result["kind"] == "direct_approval_required"
    assert runtime.calls == []
    request = approval_world["svc"].get_request(result["approval_request_id"])
    assert request is not None
    assert request.status == "pending"
    assert request.scope.get("source") == "hub_direct_execution"
    assert request.tool_name == "git.add_selected"
    assert request.arguments_digest


def test_granted_request_allows_same_digest_call(approval_world, tmp_path):
    runtime = CountingRuntime()
    adapter = _adapter(runtime)
    pending = _request_direct(adapter, tmp_path)
    approval_world["svc"].decide_request(pending["approval_request_id"], decision="granted", decided_by="operator")

    result = _request_direct(adapter, tmp_path)
    assert result["kind"] == "direct_tool_result"
    assert runtime.calls and runtime.calls[0]["tool_name"] == "git.add_selected"
    # One-shot grant is consumed after successful execution.
    request = approval_world["svc"].get_request(pending["approval_request_id"])
    assert request.status == "consumed"


def test_digest_mismatch_does_not_use_grant(approval_world, tmp_path):
    runtime = CountingRuntime()
    adapter = _adapter(runtime)
    pending = _request_direct(adapter, tmp_path)
    approval_world["svc"].decide_request(pending["approval_request_id"], decision="granted", decided_by="operator")

    result = _request_direct(adapter, tmp_path, arguments={"paths": ["b.txt", "andere.txt"]})
    assert result["kind"] == "direct_approval_required"
    assert runtime.calls == []


def test_denied_request_keeps_tool_blocked(approval_world, tmp_path):
    runtime = CountingRuntime()
    adapter = _adapter(runtime)
    pending = _request_direct(adapter, tmp_path)
    approval_world["svc"].decide_request(pending["approval_request_id"], decision="denied", decided_by="operator")

    result = _request_direct(adapter, tmp_path)
    assert result["kind"] == "direct_approval_required"
    assert runtime.calls == []


def test_no_raw_prompts_or_arguments_in_audit(approval_world, tmp_path):
    adapter = _adapter(CountingRuntime())
    _request_direct(adapter, tmp_path)
    for action, details in approval_world["audit"]:
        assert "prompt" not in {k.lower() for k in details}
        serialized = str(details)
        assert "raw_prompt" not in serialized
