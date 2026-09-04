from __future__ import annotations

import json

from agent.cli.commands import optimization


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, path, *, params=None):
        self.calls.append(("GET", path, params))
        return {"status": "success", "data": {"state": "disabled"}}

    def post(self, path, *, json=None):
        self.calls.append(("POST", path, json))
        return {"status": "success", "data": {"accepted": True}}


def test_optimization_cli_uses_same_hub_api_without_interaction(monkeypatch, capsys, tmp_path) -> None:
    client = FakeClient()
    monkeypatch.setattr("agent.cli.api_client.get_api_client", lambda: client)
    assert optimization.dispatch(["capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    spec = tmp_path / "spec.json"
    spec.write_text('{"schema":"x"}')
    assert optimization.dispatch(["dry-run", "--spec", str(spec)]) == 0
    assert client.calls[-1] == (
        "POST",
        "/api/dspy-optimization/dry-run",
        {"spec": {"schema": "x"}},
    )
    assert optimization.dispatch(
        ["provenance", "--tenant-id", "tenant-1", "--scope-id", "planning-en"]
    ) == 0
    assert client.calls[-1] == (
        "GET",
        "/api/dspy-optimization/provenance",
        {"tenant_id": "tenant-1", "scope_id": "planning-en"},
    )


def test_optimization_cli_help_is_offline_and_all_documents_are_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "agent.cli.api_client.get_api_client", lambda: (_ for _ in ()).throw(AssertionError("network must not load"))
    )
    assert optimization.dispatch(["--help"]) == 0
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]")
    assert optimization.dispatch(["dry-run", "--spec", str(invalid)]) == 2
