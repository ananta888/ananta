from __future__ import annotations

from types import SimpleNamespace

from worker.adapters.shellgpt_adapter import ShellGptAdapter


def test_shellgpt_adapter_is_optional_and_degrades_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("worker.adapters.shellgpt_adapter.shutil.which", lambda _: None)
    adapter = ShellGptAdapter(enabled=True)
    descriptor = adapter.descriptor()
    assert descriptor.enabled is False
    result = adapter.propose_patch(task_id="T1", capability_id="worker.patch.propose", prompt="x")
    assert result["status"] == "degraded"


def test_shellgpt_adapter_plan_is_plan_only(monkeypatch) -> None:
    monkeypatch.setattr("worker.adapters.shellgpt_adapter.shutil.which", lambda _: "/usr/bin/sgpt")
    monkeypatch.setattr(
        "worker.adapters.shellgpt_adapter.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="pytest -q\n", stderr=""),
    )
    adapter = ShellGptAdapter(enabled=True)
    artifact = adapter.plan(task_id="T1", capability_id="worker.command.plan", prompt="run tests")
    assert artifact["schema"] == "command_plan_artifact.v1"
    assert artifact["required_approval"] is True
    assert "Plan artifact only" in " ".join(artifact["expected_effects"])
