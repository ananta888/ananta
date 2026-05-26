from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="overview", header_logo_game={})


def test_tui_mail_account_create_list_status_disable_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    state = _state()
    created = execute_command(
        ":mail account create --display-name Work --host imap.example.com --port 993 --username user://alice --credential-ref secret://imap/alice",
        state,
    )
    assert created.handled is True
    created_payload = json.loads(created.message)
    account_id = str(created_payload["account"]["account_id"])

    listed = execute_command(":mail account list", created.state)
    listed_payload = json.loads(listed.message)
    assert len(listed_payload["accounts"]) == 1
    assert "password':" not in str(listed_payload["accounts"][0]).lower()

    status = execute_command(":mail account status", listed.state)
    status_payload = json.loads(status.message)
    assert status_payload["accounts"][0]["state"] in {"offline", "connected", "syncing", "disabled"}

    disabled = execute_command(f":mail account disable {account_id}", status.state)
    disabled_payload = json.loads(disabled.message)
    assert disabled_payload["account"]["enabled"] is False

    deleted = execute_command(f":mail account delete {account_id}", disabled.state)
    deleted_payload = json.loads(deleted.message)
    assert deleted_payload["deleted_account_id"] == account_id

    listed_empty = execute_command(":mail account list", deleted.state)
    listed_empty_payload = json.loads(listed_empty.message)
    assert listed_empty_payload["accounts"] == []


def test_tui_mail_account_rejects_password_argument(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = execute_command(
        ":mail account create --display-name Work --host imap.example.com --port 993 --username user://alice --password secret",
        _state(),
    )
    assert result.handled is False
    assert "credential_ref" in result.message
