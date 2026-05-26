from __future__ import annotations

import json

from agent import cli_goals
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def test_source_pack_default_cli_tui_e2e(monkeypatch, tmp_path, capsys) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    try:
        cli_goals.main(["sources", "bootstrap", "ananta-dev-default", "--dry-run"])
    except SystemExit as exc:
        assert int(exc.code) == 0
    out_dry_run = capsys.readouterr().out
    assert "status: planned" in out_dry_run
    assert "source_pack_id: ananta-dev-default" in out_dry_run

    try:
        cli_goals.main(["sources", "bootstrap", "ananta-dev-default"])
    except SystemExit as exc:
        assert int(exc.code) == 0
    out_boot = capsys.readouterr().out
    assert "status: ok" in out_boot
    assert "bundle_id:" in out_boot

    try:
        cli_goals.main(["sources", "doctor", "ananta-dev-default", "--json"])
    except SystemExit as exc:
        assert int(exc.code) == 0
    doctor = json.loads(capsys.readouterr().out.strip())
    assert doctor["status"] == "ready"
    assert doctor["source_pack_id"] == "ananta-dev-default"

    state = OperatorState(endpoint="http://localhost")
    query = execute_command(":sources pack query ananta-dev-default How to create an Eclipse plugin extension point?", state)
    assert query.handled is True
    payload = json.loads(query.message)
    assert payload["status"] == "ok"
    assert "eclipse" in list(payload.get("origins") or [])
    refs = list(payload.get("source_references") or [])
    assert refs
    assert any(str(item.get("source_id") or "").startswith("eclipse-") for item in refs)
    assert all(str(item.get("source_pack_id") or "") == "ananta-dev-default" for item in refs)
    assert all("context_hash" in item for item in refs)
