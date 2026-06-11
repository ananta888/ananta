"""HDE-018: mutation gate and workspace audit for custom tool execution."""
import pytest

from agent.common import audit as audit_module
from agent.services.custom_tool_executor import CustomToolExecutor


@pytest.fixture
def captured_audit(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit_module, "log_audit", lambda action, details=None: events.append((action, details or {})))
    return events


def _spec(**overrides):
    spec = {
        "name": "custom.touch_file",
        "risk_class": "execution",
        "category": "controlled_execution",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {}},
        "execution_kind": "command_template",
        "command_template": ["touch", "out.txt"],
        "path_arguments": [],
        "allowed_paths": [],
        "denied_paths": [],
        "timeout_seconds": 5,
        "output_max_chars": 1000,
    }
    spec.update(overrides)
    return spec


def _run(tmp_path, spec):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return CustomToolExecutor(tmp_path / "data").execute_spec(
        spec=spec, arguments={}, workspace_dir=str(ws), tool_call_id="t-1", config={"task_id": "task-7"}
    )


def test_read_only_tool_with_unexpected_mutation_is_blocked(tmp_path, captured_audit):
    result = _run(tmp_path, _spec())
    assert result["status"] == "rejected"
    assert result["error"] == "read_only_tool_mutated_workspace"
    actions = [action for action, _ in captured_audit]
    assert "workspace_baseline_created" in actions
    assert "workspace_mutation_blocked" in actions
    blocked = next(details for action, details in captured_audit if action == "workspace_mutation_blocked")
    assert blocked["changed_paths"] == ["out.txt"]
    assert blocked["task_id"] == "task-7"


def test_declared_controlled_write_within_allowed_paths_passes(tmp_path, captured_audit):
    spec = _spec(mutation_declaration="controlled_write", allowed_paths=["out.txt"])
    result = _run(tmp_path, spec)
    assert result["status"] == "ok"
    assert result["data"]["changed_paths"] == ["out.txt"]
    actions = [action for action, _ in captured_audit]
    assert "workspace_mutation_evaluated" in actions
    assert "workspace_mutation_blocked" not in actions


def test_undeclared_path_mutation_is_blocked(tmp_path, captured_audit):
    spec = _spec(mutation_declaration="controlled_write", allowed_paths=["andere.txt"])
    result = _run(tmp_path, spec)
    assert result["status"] == "rejected"
    assert result["error"] == "undeclared_workspace_mutation"
    actions = [action for action, _ in captured_audit]
    assert "workspace_mutation_blocked" in actions


def test_read_only_tool_without_mutation_passes(tmp_path, captured_audit):
    spec = _spec(command_template=["true"])
    result = _run(tmp_path, spec)
    assert result["status"] == "ok"
    actions = [action for action, _ in captured_audit]
    assert "workspace_mutation_evaluated" in actions
    assert "workspace_mutation_blocked" not in actions
