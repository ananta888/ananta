from __future__ import annotations

from agent.cli_backends.model_inventory import (
    CliBackendModelInventoryAdapter,
    CliRuntimeStatusCache,
)


def test_cli_inventory_uses_status_projection_without_commands_or_paths(monkeypatch):
    calls = []

    def status_loader():
        calls.append(True)
        return {
            "codex": {
                "binary_available": True,
                "binary_path": "/secret/home/bin/codex",
                "target_is_local": True,
                "auth_mode": "chatgpt_login",
                "last_success_at": None,
                "diagnostics": [],
            }
        }

    monkeypatch.setattr(
        "agent.cli_backends.model_inventory.settings.codex_default_model",
        "gpt-5-codex",
    )
    adapter = CliBackendModelInventoryAdapter(
        "codex", CliRuntimeStatusCache(status_loader)
    )
    first = adapter.collect()
    second = adapter.collect()
    wire = first.models[0].model_dump(mode="json", by_alias=True)

    assert calls == [True]
    assert first == second
    assert wire["executor_id"] == "cli:codex"
    assert wire["model_id"] == "gpt-5-codex"
    assert wire["listing_supported"] is False
    assert wire["availability"] == "unknown"
    assert "/secret/home" not in str(wire)
    assert "binary_path" not in wire
