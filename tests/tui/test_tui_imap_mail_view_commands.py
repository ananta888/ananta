from __future__ import annotations

import json
from pathlib import Path

from agent.services.imap_account_service import create_imap_account
from agent.services.imap_metadata_store_service import ImapMetadataStore
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.models import PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="overview", header_logo_game={})


def _insert_messages(tmp_path: Path, *, account_id: str, count: int = 1) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "data" / "imap" / "mail-metadata.json")
    for uid in range(1, count + 1):
        store.upsert_message(
            message_ref={
                "account_id": account_id,
                "mailbox": "INBOX",
                "uid": uid,
                "message_id": f"<m{uid}@example.com>",
                "date": "2026-05-27T00:00:00Z",
                "from": "alice@example.com",
                "to": "team@example.com",
                "subject_hash": f"s{uid}",
            },
            header_meta={"subject": f"Subject {uid}", "unread": uid % 2 == 0, "starred": uid == 1},
        )
        store.store_body(
            account_id=account_id,
            mailbox="INBOX",
            uid=uid,
            body=f"Body {uid} token=secret-{uid}",
            release_scope="body_excerpt",
        )
        store.store_attachments(
            account_id=account_id,
            mailbox="INBOX",
            uid=uid,
            attachments=[
                {
                    "filename": f"report-{uid}.txt",
                    "content_type": "text/plain",
                    "size": 12,
                    "content": f"attachment-{uid}",
                }
            ],
        )


def test_mail_view_renders_empty_and_filled_mailbox(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    opened_empty = execute_command(":mail", _state())
    payload_empty = json.loads(opened_empty.message)
    assert payload_empty["mail_mode"] is True
    rendered_empty = render_operator_shell(opened_empty.state.with_updates(section_id="artifacts"), width=180, height=44)
    assert "Mail" in rendered_empty
    assert "no mail messages" in rendered_empty

    _insert_messages(tmp_path, account_id=str(account.get("account_id") or ""), count=2)
    opened_filled = execute_command(":mail", opened_empty.state)
    rendered_filled = render_operator_shell(opened_filled.state.with_updates(section_id="artifacts"), width=180, height=44)
    assert "Mailbox list" in rendered_filled
    assert "Subject 1" in rendered_filled
    assert "Attachments:" in rendered_filled


def test_mail_navigation_scrolling_and_detail_load_are_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    _insert_messages(tmp_path, account_id=str(account.get("account_id") or ""), count=25)

    opened = execute_command(":mail", _state())
    filtered = execute_command(":mail filter unread=true", opened.state)
    filtered_payload = json.loads(filtered.message)
    assert filtered_payload["filters"]["unread"] is True

    scrolled = execute_command(":mail scroll 5", filtered.state)
    scrolled_payload = json.loads(scrolled.message)
    assert int(scrolled_payload["list_offset"]) == 5
    target_uid = int(scrolled_payload["messages"][0]["message_ref"]["uid"])

    opened_detail = execute_command(f":mail open {target_uid}", scrolled.state)
    opened_payload = json.loads(opened_detail.message)
    assert opened_payload["selected_detail"]["body_loaded"] is False

    loaded = execute_command(":mail load-body", opened_detail.state)
    loaded_payload = json.loads(loaded.message)
    detail = dict(loaded_payload["payload"]["selected_detail"])
    assert detail["body_loaded"] is True
    assert f"secret-{target_uid}" not in str(detail["body_text"])
    assert "[REDACTED_SECRET]" in str(detail["body_text"])


def test_mail_renderer_shows_offline_account_and_degraded_panel_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    opened = execute_command(":mail", _state())
    rendered = render_operator_shell(opened.state.with_updates(section_id="artifacts"), width=180, height=44)
    assert "state=offline" in rendered

    degraded_state = opened.state.with_updates(
        section_id="artifacts",
        panel_states={"artifacts": PanelState.DEGRADED},
        status_message="imap degraded",
    )
    degraded_rendered = render_operator_shell(degraded_state, width=180, height=44)
    assert "degraded" in degraded_rendered.lower()
