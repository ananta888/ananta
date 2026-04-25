from __future__ import annotations

from types import SimpleNamespace

from worker.adapters.aider_adapter import AiderAdapter


def test_aider_adapter_disabled_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr("worker.adapters.aider_adapter.shutil.which", lambda _: None)
    adapter = AiderAdapter(enabled=True)
    descriptor = adapter.descriptor()
    assert descriptor.enabled is False
    assert descriptor.kind == "unavailable"
    result = adapter.propose_patch(task_id="T1", capability_id="worker.patch.propose", prompt="x")
    assert result["status"] == "degraded"


def test_aider_adapter_returns_patch_artifact_when_available(monkeypatch) -> None:
    monkeypatch.setattr("worker.adapters.aider_adapter.shutil.which", lambda _: "/usr/bin/aider")
    monkeypatch.setattr(
        "worker.adapters.aider_adapter.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="diff --git a/a b/a\n", stderr=""),
    )
    adapter = AiderAdapter(enabled=True)
    artifact = adapter.propose_patch(
        task_id="T1",
        capability_id="worker.patch.propose",
        prompt="change file",
    )
    assert artifact["schema"] == "patch_artifact.v1"
    assert artifact["task_id"] == "T1"
