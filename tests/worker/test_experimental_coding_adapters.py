from __future__ import annotations

from worker.adapters.copilot_cli_adapter import CopilotCliAdapter
from worker.adapters.opencode_adapter import OpenCodeAdapter


def test_copilot_cli_adapter_is_experimental_and_opt_in() -> None:
    adapter = CopilotCliAdapter()
    descriptor = adapter.descriptor()
    assert descriptor.kind == "experimental"
    assert descriptor.enabled is False
    plan = adapter.plan(task_id="T1", capability_id="worker.command.plan", prompt="explain")
    assert plan["required_approval"] is True
    degraded = adapter.propose_patch(task_id="T1", capability_id="worker.patch.propose", prompt="x")
    assert degraded["status"] == "degraded"


def test_opencode_adapter_handles_unavailable_state(monkeypatch) -> None:
    monkeypatch.setattr("worker.adapters.opencode_adapter.shutil.which", lambda _: None)
    adapter = OpenCodeAdapter(enabled=True)
    descriptor = adapter.descriptor()
    assert descriptor.kind == "experimental"
    assert descriptor.enabled is False
    assert "unavailable" in descriptor.reason
    result = adapter.propose_patch(task_id="T1", capability_id="worker.patch.propose", prompt="x")
    assert result["status"] == "degraded"
