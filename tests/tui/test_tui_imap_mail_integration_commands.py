from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.imap_account_service import create_imap_account
from agent.services.imap_metadata_store_service import ImapMetadataStore
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="overview", header_logo_game={})


def _insert_messages(tmp_path: Path, *, account_id: str) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "data" / "imap" / "mail-metadata.json")
    store.upsert_message(
        message_ref={
            "account_id": account_id,
            "mailbox": "INBOX",
            "uid": 11,
            "message_id": "<m11@example.com>",
            "date": "2026-05-27T00:00:00Z",
            "from": "alerts@example.com",
            "to": "team@example.com",
            "subject_hash": "s11",
        },
        header_meta={"subject": "Build failed in CI", "unread": True, "starred": False},
    )
    store.store_body(
        account_id=account_id,
        mailbox="INBOX",
        uid=11,
        body="token=supersecret build error",
        release_scope="body_excerpt",
    )
    store.store_attachments(
        account_id=account_id,
        mailbox="INBOX",
        uid=11,
        attachments=[
            {
                "filename": "trace.log",
                "content_type": "text/plain",
                "size": 20,
                "content": "stacktrace",
            },
            {
                "filename": "../evil.sh",
                "content_type": "text/x-shellscript",
                "size": 12,
                "content": "echo hi",
            },
        ],
    )


def test_mail_search_command_and_renderer(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    _insert_messages(tmp_path, account_id=str(account.get("account_id") or ""))
    opened = execute_command(":mail", _state())
    searched = execute_command(":mail search from:alerts subject:build mailbox:INBOX unread:true", opened.state)
    payload = json.loads(searched.message)
    assert payload["last_search_query"]
    assert payload["search_result_refs"]
    rendered = render_operator_shell(searched.state.with_updates(section_id="artifacts"), width=180, height=44)
    assert "Search: query=" in rendered
    assert "Build failed in CI" in rendered


def test_mail_note_link_artifact_grant_revoke_and_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    _insert_messages(tmp_path, account_id=str(account.get("account_id") or ""))
    opened = execute_command(":mail", _state())
    selected = execute_command(":mail open 11", opened.state)

    noted = execute_command(":mail note add investigate-ci-mail", selected.state)
    note_payload = json.loads(noted.message)
    assert len(note_payload["notes"]) == 1

    linked = execute_command(":mail link-current-to-goal goal-mail-1", noted.state)
    link_payload = json.loads(linked.message)
    account_id = str(account.get("account_id") or "")
    assert f"goal-mail-1:mail://{account_id}/INBOX/11" in link_payload["linked_goal_refs"]

    loaded = execute_command(":mail load-body", linked.state)
    loaded_payload = json.loads(loaded.message)
    assert loaded_payload["payload"]["selected_detail"]["body_loaded"] is True

    registered = execute_command(":mail artifact register-current --scope excerpt", loaded.state)
    registered_payload = json.loads(registered.message)
    artifact_ref = str(registered_payload["artifact"]["artifact_ref"])
    assert artifact_ref.startswith(f"mail://{account_id}/INBOX/11")

    granted = execute_command(":mail grant-current-to-goal goal-mail-1 --scope metadata_only", registered.state)
    granted_payload = json.loads(granted.message)
    grant_id = str(granted_payload["grant"]["grant_id"])
    graph = GoalArtifactService().get_goal_graph("goal-mail-1")
    assert any(str(item.get("grant_id") or "") == grant_id for item in list(graph.get("source_grants") or []))

    context_cloud = execute_command(":mail context-envelope goal-mail-1 --target cloud_worker", granted.state)
    cloud_payload = json.loads(context_cloud.message)
    assert cloud_payload["allowed"] is False

    context_local = execute_command(":mail context-envelope goal-mail-1 --target local_worker", context_cloud.state)
    local_payload = json.loads(context_local.message)
    assert local_payload["allowed"] is True
    assert any(str(item).startswith(f"mail://{account_id}/INBOX/11") for item in local_payload["mail_source_refs"])

    revoked = execute_command(f":mail revoke-grant goal-mail-1 {grant_id}", context_local.state)
    revoked_payload = json.loads(revoked.message)
    assert revoked_payload["grant_id"] == grant_id


def test_mail_attachment_download_register_export_and_snake_explain(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    _insert_messages(tmp_path, account_id=str(account.get("account_id") or ""))

    opened = execute_command(":mail", _state())
    selected = execute_command(":mail open 11", opened.state)
    listed = execute_command(":mail attachment list", selected.state)
    listed_payload = json.loads(listed.message)
    assert len(listed_payload["attachments"]) == 2

    downloaded = execute_command(":mail attachment download trace.log", listed.state)
    downloaded_payload = json.loads(downloaded.message)
    assert downloaded_payload["download"]["filename"] == "trace.log"
    assert downloaded_payload["download"]["sha256"]

    registered_attachment = execute_command(":mail attachment register trace.log", downloaded.state)
    attachment_payload = json.loads(registered_attachment.message)
    assert attachment_payload["artifact"]["artifact_kind"] == "attachment_ref"

    loaded = execute_command(":mail load-body", registered_attachment.state)
    exported = execute_command(":mail export current --format json --include-body --confirm-body --goal goal-mail-export", loaded.state)
    export_payload = json.loads(exported.message)
    assert export_payload["export"]["format"] == "json"
    assert export_payload["goal_output_artifact"]["goal_id"] == "goal-mail-export"

    explained = execute_command(":mail snake-explain", loaded.state)
    explain_payload = json.loads(explained.message)
    assert explain_payload["ok"] is True
    assert explain_payload["auto_inbox_summary"] is False
    assert explain_payload["mail_source_refs"]
