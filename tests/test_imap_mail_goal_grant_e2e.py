from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.imap_account_service import create_imap_account
from agent.services.imap_connector_service import ImapConnectorService, StaticImapClient
from agent.services.imap_mail_artifact_service import get_mail_artifact
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope
from agent.services.imap_metadata_store_service import ImapMetadataStore
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


class _Factory:
    def __init__(self, client: StaticImapClient) -> None:
        self._client = client

    def create(self) -> StaticImapClient:
        return self._client


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="overview", header_logo_game={})


def _sync_test_mail_from_mock_imap(*, repo_root: Path, account_id: str) -> None:
    connector = ImapConnectorService(
        client_factory=_Factory(
            StaticImapClient(
                mailboxes=["INBOX"],
                headers={
                    "INBOX": [
                        {
                            "uid": 31,
                            "subject": "CI failed",
                            "message_id": "<m31@example.com>",
                            "date": "2026-05-27T00:00:00Z",
                            "from": "alerts@example.com",
                            "to": "team@example.com",
                            "attachments": [{"filename": "trace.log", "content_type": "text/plain", "size": 45}],
                        }
                    ]
                },
                bodies={"INBOX": {31: "token=secret-ci build failed in pipeline"}},
            )
        )
    )
    connected = connector.connect_account(
        account={
            "account_id": account_id,
            "display_name": "Work",
            "host": "imap.example.com",
            "port": 993,
            "username_ref": "user://alice",
            "credential_ref": "secret://imap/alice",
            "auth_mode": "password_app_token",
            "tls_mode": "require_tls",
            "sync_policy": "headers_only",
            "enabled": True,
        },
        credential="token",
        username="alice",
    )
    assert connected["ok"] is True
    session = connected["session"]
    mailboxes = connector.list_mailboxes(session)
    assert mailboxes["ok"] is True
    headers = connector.fetch_headers(session, mailbox="INBOX", limit=20)
    assert headers["ok"] is True
    store = ImapMetadataStore(store_path=repo_root / "data" / "imap" / "mail-metadata.json")
    for item in list(headers["headers"]):
        uid = int(item["uid"])
        store.upsert_message(
            message_ref={
                "account_id": account_id,
                "mailbox": "INBOX",
                "uid": uid,
                "message_id": str(item.get("message_id") or ""),
                "date": str(item.get("date") or "2026-05-27T00:00:00Z"),
                "from": str(item.get("from") or "alerts@example.com"),
                "to": str(item.get("to") or "team@example.com"),
                "subject_hash": f"s{uid}",
            },
            header_meta={"subject": str(item.get("subject") or ""), "unread": True, "starred": False},
        )
        store.store_attachments(
            account_id=account_id,
            mailbox="INBOX",
            uid=uid,
            attachments=[dict(att) for att in list(item.get("attachments") or []) if isinstance(att, dict)],
        )
        body = connector.fetch_body(session, mailbox="INBOX", uid=uid)
        assert body["ok"] is True
        store.store_body(
            account_id=account_id,
            mailbox="INBOX",
            uid=uid,
            body=str(body["body"]),
            release_scope="body_excerpt",
        )


def test_e2e_mail_read_and_goal_grant_uses_only_explicit_excerpt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    account = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    account_id = str(account.get("account_id") or "")
    _sync_test_mail_from_mock_imap(repo_root=tmp_path, account_id=account_id)

    opened = execute_command(":mail", _state())
    opened_payload = json.loads(opened.message)
    assert opened_payload["messages"]
    assert int(opened_payload["messages"][0]["message_ref"]["uid"]) == 31

    selected = execute_command(":mail open 31", opened.state)
    selected_payload = json.loads(selected.message)
    assert selected_payload["selected_detail"]["body_loaded"] is False

    loaded = execute_command(":mail load-body", selected.state)
    loaded_payload = json.loads(loaded.message)
    detail = dict(dict(loaded_payload["payload"]).get("selected_detail") or {})
    assert detail["body_loaded"] is True
    assert "secret-ci" not in str(detail["body_text"])
    assert "[REDACTED_SECRET]" in str(detail["body_text"])

    granted = execute_command(":mail grant-current-to-goal goal-imap-e2e --scope excerpt", loaded.state)
    granted_payload = json.loads(granted.message)
    artifact_ref = str(dict(granted_payload["grant"]).get("artifact_ref") or "")
    graph = GoalArtifactService().get_goal_graph("goal-imap-e2e")
    assert any(str(item.get("artifact_ref") or "") == artifact_ref for item in list(graph.get("source_grants") or []))

    artifact = get_mail_artifact(artifact_ref=artifact_ref, repo_root=tmp_path)
    assert artifact is not None
    assert str(artifact.get("artifact_kind") or "") == "excerpt"
    assert "secret-ci" not in str(artifact.get("excerpt") or "")

    worker_context = build_mail_context_envelope(goal_id="goal-imap-e2e", worker_target="local_worker", repo_root=str(tmp_path))
    assert worker_context["allowed"] is True
    assert worker_context["mail_source_refs"] == [artifact_ref]
    assert worker_context["mail_artifacts"][0]["artifact_kind"] == "excerpt"
