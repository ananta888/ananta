"""Mail command handler for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.services.imap_account_service import (
    create_imap_account,
    delete_imap_account,
    disable_imap_account,
    list_imap_accounts,
)
from agent.services.imap_attachment_service import attachment_metadata, download_attachment_securely
from agent.services.imap_export_service import export_mail_payload
from agent.services.imap_feature_flag_service import resolve_imap_runtime_state
from agent.services.imap_mail_artifact_service import get_mail_artifact, list_mail_artifacts, register_mail_artifact
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope
from agent.services.imap_metadata_store_service import ImapMetadataStore
from agent.services.imap_redaction_pipeline_service import redact_mail_for_worker_context
from agent.services.imap_search_service import search_mail_metadata
from agent.services.imap_snake_assist_service import explain_mail_for_snake_assist
from agent.services.imap_threading_service import annotate_messages_with_thread_counts
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState, PanelState


def _mail_repo_root() -> Path:
    return Path.cwd()


def _mail_store(repo_root: Path) -> ImapMetadataStore:
    return ImapMetadataStore(store_path=repo_root / "data" / "imap" / "mail-metadata.json")


def _mail_message_key(row: dict[str, object]) -> str:
    ref = dict(row.get("message_ref") or {})
    message_id = str(ref.get("message_id") or "").strip()
    if message_id:
        return message_id
    return f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"


def _build_mail_payload(*, game: dict[str, object], repo_root: Path) -> dict[str, object]:
    accounts = list_imap_accounts(repo_root=repo_root)
    selected_account_id = str(game.get("mail_selected_account_id") or "").strip()
    if not selected_account_id and accounts:
        selected_account_id = str(accounts[0].get("account_id") or "")
        game["mail_selected_account_id"] = selected_account_id
    selected_account = next(
        (item for item in accounts if str(item.get("account_id") or "") == selected_account_id),
        dict(accounts[0]) if accounts else {},
    )
    cfg = dict(game.get("imap_config") or {"imap": {"enabled": True}})
    connected = {str(item) for item in list(game.get("imap_connected_account_ids") or []) if str(item).strip()}
    syncing = {str(item) for item in list(game.get("imap_syncing_account_ids") or []) if str(item).strip()}
    account_status_rows: list[dict[str, object]] = []
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        if not bool(account.get("enabled", True)):
            state_row = {"state": "disabled", "reason_code": "account_disabled"}
        else:
            state_row = resolve_imap_runtime_state(
                cfg,
                has_account=True,
                connected=account_id in connected,
                syncing=account_id in syncing,
            )
        account_status_rows.append(
            {
                "account_id": account_id,
                "display_name": str(account.get("display_name") or ""),
                "enabled": bool(account.get("enabled", True)),
                **state_row,
            }
        )

    store = _mail_store(repo_root)
    rows = store.list_messages()
    mock_rows = [dict(item) for item in list(game.get("mail_mock_messages") or []) if isinstance(item, dict)]
    if mock_rows:
        rows.extend(mock_rows)
    if selected_account_id:
        rows = [item for item in rows if str(dict(item.get("message_ref") or {}).get("account_id") or "") == selected_account_id]
    mailbox_set = sorted(
        {
            str(dict(item.get("message_ref") or {}).get("mailbox") or "").strip()
            for item in rows
            if str(dict(item.get("message_ref") or {}).get("mailbox") or "").strip()
        }
    )
    if not mailbox_set:
        mock_mailboxes = dict(game.get("mail_mock_mailboxes_by_account") or {})
        mailbox_set = [
            str(item).strip()
            for item in list(mock_mailboxes.get(selected_account_id) or ["INBOX"])
            if str(item).strip()
        ]
    selected_mailbox = str(game.get("mail_selected_mailbox") or "").strip()
    if not selected_mailbox and mailbox_set:
        selected_mailbox = mailbox_set[0]
        game["mail_selected_mailbox"] = selected_mailbox

    filters = dict(game.get("mail_filters") or {})
    query_filters = dict(filters)
    if selected_mailbox:
        query_filters.setdefault("mailbox", selected_mailbox)
    search_rows = rows
    if not mock_rows:
        by_key: dict[str, dict[str, object]] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ref = dict(raw.get("message_ref") or {})
            key = f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"
            by_key[key] = dict(raw)
        search = search_mail_metadata(
            store=store,
            filters=query_filters,
            include_body_search=False,
        )
        search_rows = [
            {
                **dict(
                    by_key.get(
                        f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                        f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                        f"{dict(item.get('message_ref') or {}).get('uid')}",
                        {},
                    )
                ),
                "message_ref": dict(item.get("message_ref") or {}),
                "header_meta": dict(item.get("header_meta") or {}),
                "stale": bool(item.get("stale", False)),
                "body_scope": str(item.get("policy_state") or "metadata_only"),
                "source_ref": str(item.get("source_ref") or ""),
                "body": str(
                    dict(
                        by_key.get(
                            f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                            f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                            f"{dict(item.get('message_ref') or {}).get('uid')}",
                            {},
                        )
                    ).get("body")
                    or ""
                ),
                "attachments": [
                    dict(att)
                    for att in list(
                        dict(
                            by_key.get(
                                f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                                f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                                f"{dict(item.get('message_ref') or {}).get('uid')}",
                                {},
                            )
                        ).get("attachments")
                        or []
                    )
                    if isinstance(att, dict)
                ],
            }
            for item in list(search.get("results") or [])
            if isinstance(item, dict)
        ]
    else:
        def _match_row(row: dict[str, object]) -> bool:
            ref = dict(row.get("message_ref") or {})
            header = dict(row.get("header_meta") or {})
            mailbox = str(ref.get("mailbox") or "")
            if query_filters.get("mailbox") and mailbox != str(query_filters.get("mailbox")):
                return False
            if query_filters.get("from") and str(query_filters.get("from")).lower() not in str(ref.get("from") or "").lower():
                return False
            if query_filters.get("subject") and str(query_filters.get("subject")).lower() not in str(header.get("subject") or "").lower():
                return False
            unread = query_filters.get("unread")
            if unread is not None and bool(header.get("unread")) is not bool(unread):
                return False
            return True

        search_rows = [row for row in rows if _match_row(row)]

    threaded_rows = annotate_messages_with_thread_counts(search_rows)
    offset = max(0, int(game.get("mail_list_offset") or 0))
    page_size = 20
    page_rows = threaded_rows[offset : offset + page_size]
    selected_message_key = str(game.get("mail_selected_message_key") or "").strip()
    selected_row = next((row for row in threaded_rows if _mail_message_key(row) == selected_message_key), dict(page_rows[0]) if page_rows else {})
    selected_detail = {
        "message_ref": dict(selected_row.get("message_ref") or {}),
        "header_meta": dict(selected_row.get("header_meta") or {}),
        "body_scope": str(selected_row.get("body_scope") or "metadata_only"),
        "redaction_status": str(game.get("mail_detail_redaction_status") or selected_row.get("redaction_status") or "not_required"),
        "body_loaded": bool(game.get("mail_detail_body_loaded", False)),
        "body_text": str(game.get("mail_detail_body") or "") if bool(game.get("mail_detail_body_loaded", False)) else "",
        "attachments": attachment_metadata([dict(item) for item in list(selected_row.get("attachments") or []) if isinstance(item, dict)]),
        "attachment_downloaded": dict(game.get("mail_attachment_last_download") or {}),
    }
    current_artifact = get_mail_artifact(
        artifact_ref=str(game.get("mail_current_artifact_ref") or ""),
        repo_root=repo_root,
    )
    return {
        "mail_mode": True,
        "accounts": account_status_rows,
        "selected_account_id": selected_account_id,
        "selected_account": selected_account,
        "mailboxes": mailbox_set,
        "selected_mailbox": selected_mailbox,
        "filters": filters,
        "list_offset": offset,
        "total_messages": len(threaded_rows),
        "messages": page_rows,
        "selected_message_key": _mail_message_key(selected_row) if selected_row else "",
        "selected_detail": selected_detail,
        "last_search_query": str(game.get("mail_last_search_query") or ""),
        "search_result_refs": [str(item) for item in list(game.get("mail_search_result_refs") or []) if str(item).strip()],
        "notes": [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, dict)],
        "linked_goal_refs": [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()],
        "current_artifact_ref": str(game.get("mail_current_artifact_ref") or ""),
        "current_artifact": dict(current_artifact or {}) if isinstance(current_artifact, dict) else {},
        "artifact_count": len(list_mail_artifacts(repo_root=repo_root)),
    }



def handle_mail_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :mail subcommands."""
    repo_root = _mail_repo_root()
    game = dict(state.header_logo_game or {})
    if not args:
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message="mail view opened",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    sub = str(args[0]).lower()

    def _option(tokens: list[str], name: str) -> str:
        key = f"--{name}"
        for idx, token in enumerate(tokens):
            if str(token).strip().lower() == key and idx + 1 < len(tokens):
                return str(tokens[idx + 1]).strip()
        return ""

    if sub == "account":
        if len(args) < 2:
            return CommandResult(state, "mail account list|status|create|disable|delete|use", handled=False)
        action = str(args[1]).lower()
        if action == "list":
            accounts = list_imap_accounts(repo_root=repo_root)
            return CommandResult(
                state.with_updates(status_message=f"mail accounts={len(accounts)}"),
                json.dumps({"accounts": accounts}, ensure_ascii=False),
            )
        if action == "status":
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"mail account status rows={len(payload.get('accounts') or [])}"),
                json.dumps({"accounts": payload.get("accounts") or []}, ensure_ascii=False),
            )
        if action == "create":
            tokens = list(args[2:])
            if any(str(token).strip().lower() in {"--password", "--token"} for token in tokens):
                return CommandResult(state, "mail account create requires credential_ref, not password/token", handled=False)
            display_name = _option(tokens, "display-name")
            host = _option(tokens, "host")
            port_text = _option(tokens, "port")
            username = _option(tokens, "username")
            credential_ref = _option(tokens, "credential-ref")
            sync_policy = _option(tokens, "sync-policy") or "headers_only"
            if not (display_name and host and port_text and username and credential_ref):
                return CommandResult(
                    state,
                    "mail account create --display-name <name> --host <host> --port <port> --username <username_ref> --credential-ref <ref>",
                    handled=False,
                )
            try:
                port = int(port_text)
            except ValueError:
                return CommandResult(state, "mail account create --port must be integer", handled=False)
            try:
                account = create_imap_account(
                    repo_root=repo_root,
                    display_name=display_name,
                    host=host,
                    port=port,
                    username_ref=username,
                    credential_ref=credential_ref,
                    sync_policy=sync_policy,
                )
            except ValueError as exc:
                return CommandResult(state, f"mail account create failed: {exc}", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail account created {account.get('account_id')}",
                ),
                json.dumps({"account": account, "payload": payload}, ensure_ascii=False),
            )
        if action == "use":
            if len(args) < 3:
                return CommandResult(state, "mail account use <account-id>", handled=False)
            game["mail_selected_account_id"] = str(args[2]).strip()
            game.pop("mail_selected_mailbox", None)
            game["mail_list_offset"] = 0
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail account {args[2]} selected",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if action == "disable":
            if len(args) < 3:
                return CommandResult(state, "mail account disable <account-id>", handled=False)
            try:
                account = disable_imap_account(account_id=str(args[2]).strip(), repo_root=repo_root)
            except ValueError:
                return CommandResult(state, "mail account disable failed: imap_account_not_found", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail account disabled {account.get('account_id')}",
                ),
                json.dumps({"account": account, "payload": payload}, ensure_ascii=False),
            )
        if action == "delete":
            if len(args) < 3:
                return CommandResult(state, "mail account delete <account-id>", handled=False)
            try:
                account = delete_imap_account(account_id=str(args[2]).strip(), repo_root=repo_root)
            except ValueError:
                return CommandResult(state, "mail account delete failed: imap_account_not_found", handled=False)
            if str(game.get("mail_selected_account_id") or "") == str(account.get("account_id") or ""):
                game.pop("mail_selected_account_id", None)
                game.pop("mail_selected_mailbox", None)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail account deleted {account.get('account_id')}",
                ),
                json.dumps({"deleted_account_id": account.get("account_id"), "payload": payload}, ensure_ascii=False),
            )
        return CommandResult(state, "mail account list|status|create|use|disable|delete", handled=False)

    if sub == "mailbox":
        if len(args) < 2:
            return CommandResult(state, "mail mailbox <name>", handled=False)
        game["mail_selected_mailbox"] = str(args[1]).strip()
        game["mail_list_offset"] = 0
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail mailbox {args[1]} selected",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "scroll":
        if len(args) < 2:
            return CommandResult(state, "mail scroll <delta>", handled=False)
        try:
            delta = int(str(args[1]).strip())
        except ValueError:
            return CommandResult(state, "mail scroll <delta>", handled=False)
        game["mail_list_offset"] = max(0, int(game.get("mail_list_offset") or 0) + delta)
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail scroll offset={payload.get('list_offset')}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "filter":
        filters = dict(game.get("mail_filters") or {})
        for token in args[1:]:
            if "=" not in token:
                continue
            key, value = str(token).split("=", 1)
            normalized_key = key.strip().lower()
            normalized_value = value.strip()
            if normalized_key == "unread":
                filters["unread"] = normalized_value.lower() in {"1", "true", "yes", "on"}
            elif normalized_key in {"from", "subject", "mailbox", "to", "date_from", "date_to"}:
                filters[normalized_key] = normalized_value
        game["mail_filters"] = filters
        game["mail_list_offset"] = 0
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message="mail filters updated",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "open":
        if len(args) < 2:
            return CommandResult(state, "mail open <message-id|uid>", handled=False)
        target = str(args[1]).strip()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
        selected_row = next(
            (
                row
                for row in rows
                if _mail_message_key(row) == target or str(dict(row.get("message_ref") or {}).get("uid") or "") == target
            ),
            {},
        )
        if not selected_row:
            return CommandResult(state, "mail open failed: message not found", handled=False)
        game["mail_selected_message_key"] = _mail_message_key(selected_row)
        game["mail_detail_body_loaded"] = False
        game["mail_detail_body"] = ""
        game["mail_detail_redaction_status"] = "not_required"
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail open {target}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "load-body":
        target = str(args[1]).strip() if len(args) > 1 else str(game.get("mail_selected_message_key") or "").strip()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
        selected_row = next(
            (
                row
                for row in rows
                if _mail_message_key(row) == target or str(dict(row.get("message_ref") or {}).get("uid") or "") == target
            ),
            {},
        )
        if not selected_row:
            return CommandResult(state, "mail load-body failed: message not found", handled=False)
        ref = dict(selected_row.get("message_ref") or {})
        store_row = _mail_store(repo_root).get_by_uid(
            account_id=str(ref.get("account_id") or ""),
            mailbox=str(ref.get("mailbox") or ""),
            uid=int(ref.get("uid") or 0),
        )
        body_text = str(dict(store_row or {}).get("body") or selected_row.get("body") or "")
        redacted = redact_mail_for_worker_context(body_text=body_text, attachments=list(selected_row.get("attachments") or []))
        game["mail_selected_message_key"] = _mail_message_key(selected_row)
        game["mail_detail_body_loaded"] = True
        game["mail_detail_body"] = str(redacted.get("redacted_body") or "")
        game["mail_detail_redaction_status"] = str(redacted.get("redaction_status") or "not_required")
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail body loaded for {target}",
            ),
            json.dumps({"payload": payload, "redaction": redacted}, ensure_ascii=False),
        )
    if sub == "attachment":
        if len(args) < 2:
            return CommandResult(state, "mail attachment list|download|register ...", handled=False)
        action = str(args[1]).lower()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        message_ref = dict(detail.get("message_ref") or {})
        attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, dict)]
        if action == "list":
            return CommandResult(
                state.with_updates(status_message=f"mail attachments={len(attachments)}"),
                json.dumps({"attachments": attachments, "message_ref": message_ref}, ensure_ascii=False),
            )
        if action == "download":
            if len(args) < 3:
                return CommandResult(state, "mail attachment download <filename>", handled=False)
            filename = str(args[2]).strip()
            if not message_ref:
                return CommandResult(state, "mail attachment download failed: no selected message", handled=False)
            target = next((row for row in attachments if str(row.get("filename") or "") == filename), {})
            if not target:
                return CommandResult(state, "mail attachment download failed: attachment not found", handled=False)
            store_row = _mail_store(repo_root).get_by_uid(
                account_id=str(message_ref.get("account_id") or ""),
                mailbox=str(message_ref.get("mailbox") or ""),
                uid=int(message_ref.get("uid") or 0),
            )
            raw_attachments = [dict(item) for item in list(dict(store_row or {}).get("attachments") or []) if isinstance(item, dict)]
            raw_target: dict[str, object] = {}
            indexed_meta = attachment_metadata(raw_attachments)
            for idx, meta in enumerate(indexed_meta):
                if str(meta.get("filename") or "") == filename and idx < len(raw_attachments):
                    raw_target = dict(raw_attachments[idx])
                    break
            if not raw_target:
                return CommandResult(state, "mail attachment download failed: content missing", handled=False)
            downloaded = download_attachment_securely(
                attachment=raw_target,
                target_dir=repo_root / "data" / "imap" / "downloads",
            )
            game["mail_attachment_last_download"] = downloaded
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail attachment downloaded {filename}",
                ),
                json.dumps({"download": downloaded, "payload": payload}, ensure_ascii=False),
            )
        if action == "register":
            if len(args) < 3:
                return CommandResult(state, "mail attachment register <filename>", handled=False)
            filename = str(args[2]).strip()
            if not message_ref:
                return CommandResult(state, "mail attachment register failed: no selected message", handled=False)
            target = next((row for row in attachments if str(row.get("filename") or "") == filename), {})
            if not target:
                return CommandResult(state, "mail attachment register failed: attachment not found", handled=False)
            artifact = register_mail_artifact(
                message_ref=message_ref,
                scope="attachment_ref",
                redaction_status="not_required",
                policy_decision_ref="policy:mail:attachment_ref",
                excerpt=str(target.get("filename") or ""),
                repo_root=repo_root,
            )
            game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"mail attachment artifact registered {filename}",
                ),
                json.dumps({"artifact": artifact, "payload": payload}, ensure_ascii=False),
            )
        return CommandResult(state, "mail attachment list|download <filename>|register <filename>", handled=False)
    if sub == "export":
        if len(args) < 2 or str(args[1]).lower() != "current":
            return CommandResult(state, "mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]", handled=False)
        format_name = "json"
        include_body = False
        goal_id = ""
        for idx, token in enumerate(args[2:], start=2):
            lowered = str(token).lower()
            if lowered == "--format" and idx + 1 < len(args):
                format_name = str(args[idx + 1]).strip()
            if lowered == "--include-body":
                include_body = True
            if lowered == "--goal" and idx + 1 < len(args):
                goal_id = str(args[idx + 1]).strip()
        if include_body and "--confirm-body" not in [str(item).lower() for item in args[2:]]:
            return CommandResult(state, "mail export with body requires --confirm-body", handled=False)
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        message_ref = dict(detail.get("message_ref") or {})
        if not message_ref:
            return CommandResult(state, "mail export failed: no selected message", handled=False)
        exported = export_mail_payload(
            message_ref=message_ref,
            header_meta=dict(detail.get("header_meta") or {}),
            body_text=str(detail.get("body_text") or ""),
            format_name=format_name,
            include_body=include_body,
            export_dir=repo_root / "data" / "imap" / "exports",
        )
        output_artifact = {}
        if goal_id:
            try:
                output_artifact = GoalArtifactService().record_output_artifact(
                    goal_id=goal_id,
                    output_artifact={
                        "schema": "goal_output_artifact.v1",
                        "output_artifact_id": f"mail-export-{hashlib.sha1(str(exported.get('export_ref')).encode('utf-8')).hexdigest()[:12]}",
                        "goal_id": goal_id,
                        "artifact_type": "file",
                        "created_at": _now_iso(),
                        "artifact_ref": str(exported.get("export_ref") or ""),
                        "content_hash": str(exported.get("sha256") or ""),
                        "status": "created",
                        "provenance_summary": "mail export from operator_tui",
                        "provenance_kind": "manual",
                    },
                )
            except GoalArtifactServiceError as exc:
                return CommandResult(state, f"mail export goal artifact failed: {exc.reason_code}", handled=False)
        return CommandResult(
            state.with_updates(status_message=f"mail export {format_name}"),
            json.dumps({"export": exported, "goal_output_artifact": output_artifact}, ensure_ascii=False),
        )
    if sub == "snake-explain":
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        explain = explain_mail_for_snake_assist(
            opened=bool(detail.get("message_ref")),
            artifact_ref=str(payload.get("current_artifact_ref") or ""),
            message_ref=dict(detail.get("message_ref") or {}),
            body_text=str(detail.get("body_text") or ""),
        )
        if not bool(explain.get("ok")):
            return CommandResult(state, f"mail snake explain failed: {explain.get('reason_code')}", handled=False)
        return CommandResult(
            state.with_updates(status_message="mail snake explain ready"),
            json.dumps(explain, ensure_ascii=False),
        )
    if sub == "search":
        query = " ".join(args[1:]).strip()
        if not query:
            return CommandResult(state, "mail search <query>", handled=False)
        filters = dict(game.get("mail_filters") or {})
        filters.clear()
        for token in query.split():
            lowered = token.lower()
            if lowered.startswith("from:"):
                filters["from"] = token.split(":", 1)[1]
            elif lowered.startswith("to:"):
                filters["to"] = token.split(":", 1)[1]
            elif lowered.startswith("subject:"):
                filters["subject"] = token.split(":", 1)[1]
            elif lowered.startswith("mailbox:"):
                filters["mailbox"] = token.split(":", 1)[1]
            elif lowered.startswith("date:"):
                value = token.split(":", 1)[1]
                if ".." in value:
                    start, end = value.split("..", 1)
                    filters["date_from"] = start
                    filters["date_to"] = end
            elif lowered.startswith("unread:"):
                value = token.split(":", 1)[1]
                filters["unread"] = value.lower() in {"1", "true", "yes", "on"}
            else:
                filters["subject"] = f"{filters.get('subject', '')} {token}".strip()
        game["mail_filters"] = filters
        game["mail_list_offset"] = 0
        game["mail_last_search_query"] = query
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        refs = []
        for row in list(payload.get("messages") or []):
            if not isinstance(row, dict):
                continue
            ref = dict(row.get("message_ref") or {})
            refs.append(f"mail://{ref.get('account_id')}/{ref.get('mailbox')}/{ref.get('uid')}")
        game["mail_search_result_refs"] = refs
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail search results={len(refs)}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "note":
        if len(args) < 3 or str(args[1]).lower() != "add":
            return CommandResult(state, "mail note add <text>", handled=False)
        text = " ".join(args[2:]).strip()
        if not text:
            return CommandResult(state, "mail note add <text>", handled=False)
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        selected = dict(payload.get("selected_detail") or {}).get("message_ref") or {}
        ref = dict(selected)
        note = {
            "message_ref": {
                "account_id": str(ref.get("account_id") or ""),
                "mailbox": str(ref.get("mailbox") or ""),
                "uid": int(ref.get("uid") or 0),
                "message_id": str(ref.get("message_id") or ""),
            },
            "note": text,
            "created_at": _now_iso(),
        }
        notes = [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, dict)]
        notes.append(note)
        game["mail_notes"] = notes
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message="mail note added",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "link-current-to-goal":
        if len(args) < 2:
            return CommandResult(state, "mail link-current-to-goal <goal-id>", handled=False)
        goal_id = str(args[1]).strip()
        if not goal_id:
            return CommandResult(state, "mail link-current-to-goal <goal-id>", handled=False)
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        selected = dict(payload.get("selected_detail") or {}).get("message_ref") or {}
        if not dict(selected):
            return CommandResult(state, "mail link failed: no selected message", handled=False)
        links = [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()]
        source_ref = f"mail://{selected.get('account_id')}/{selected.get('mailbox')}/{selected.get('uid')}"
        entry = f"{goal_id}:{source_ref}"
        if entry not in links:
            links.append(entry)
        game["mail_linked_goal_refs"] = links
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail linked to goal {goal_id}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "artifact":
        if len(args) < 2:
            return CommandResult(state, "mail artifact register-current [--scope metadata_only|excerpt|full_body]", handled=False)
        action = str(args[1]).lower()
        if action != "register-current":
            return CommandResult(state, "mail artifact register-current [--scope metadata_only|excerpt|full_body]", handled=False)
        scope = "metadata_only"
        for idx, token in enumerate(args[2:], start=2):
            if str(token).lower() == "--scope" and idx + 1 < len(args):
                requested = str(args[idx + 1]).strip().lower()
                if requested == "body_excerpt":
                    requested = "excerpt"
                scope = requested
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        message_ref = dict(detail.get("message_ref") or {})
        if not message_ref:
            return CommandResult(state, "mail artifact failed: no selected message", handled=False)
        excerpt = str(detail.get("body_text") or "")
        if scope == "full_body" and "--confirm-full-body" not in [str(item).lower() for item in args[2:]]:
            return CommandResult(state, "mail artifact full_body requires --confirm-full-body", handled=False)
        artifact = register_mail_artifact(
            message_ref=message_ref,
            scope=scope,
            redaction_status=str(detail.get("redaction_status") or "not_required"),
            policy_decision_ref=f"policy:mail:{scope}",
            excerpt=excerpt,
            repo_root=repo_root,
        )
        game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail artifact registered {artifact.get('artifact_ref')}",
            ),
            json.dumps({"artifact": artifact, "payload": payload}, ensure_ascii=False),
        )
    if sub == "grant-current-to-goal":
        if len(args) < 2:
            return CommandResult(state, "mail grant-current-to-goal <goal-id> [--scope metadata_only|excerpt|full_body] [--confirm-full-body]", handled=False)
        goal_id = str(args[1]).strip()
        scope = "metadata_only"
        for idx, token in enumerate(args[2:], start=2):
            if str(token).lower() == "--scope" and idx + 1 < len(args):
                requested = str(args[idx + 1]).strip().lower()
                if requested == "body_excerpt":
                    requested = "excerpt"
                scope = requested
        if scope == "full_body" and "--confirm-full-body" not in [str(item).lower() for item in args[2:]]:
            return CommandResult(state, "mail grant full_body requires --confirm-full-body", handled=False)
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        message_ref = dict(detail.get("message_ref") or {})
        if not message_ref:
            return CommandResult(state, "mail grant failed: no selected message", handled=False)
        artifact = register_mail_artifact(
            message_ref=message_ref,
            scope=scope,
            redaction_status=str(detail.get("redaction_status") or "not_required"),
            policy_decision_ref=f"policy:mail:{scope}",
            excerpt=str(detail.get("body_text") or ""),
            repo_root=repo_root,
        )
        service = GoalArtifactService()
        artifact_ref = str(artifact.get("artifact_ref") or "")
        grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{scope}'.encode('utf-8')).hexdigest()[:10]}"
        grant_payload = {
            "schema": "source_artifact_grant.v1",
            "grant_id": grant_id,
            "goal_id": goal_id,
            "artifact_ref": artifact_ref,
            "granted_by": "operator_tui_mail",
            "granted_at": _now_iso(),
            "allowed_usages": sorted(set(["read", "use_as_context"])),
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": f"policy:mail:{scope}",
        }
        try:
            created = service.create_grant(goal_id=goal_id, grant=grant_payload)
        except GoalArtifactServiceError as exc:
            return CommandResult(state, f"mail grant failed: {exc.reason_code}", handled=False)
        game["mail_current_artifact_ref"] = artifact_ref
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"mail granted to goal {goal_id}",
            ),
            json.dumps({"grant": created, "artifact": artifact, "payload": payload}, ensure_ascii=False),
        )
    if sub == "revoke-grant":
        if len(args) < 3:
            return CommandResult(state, "mail revoke-grant <goal-id> <grant-id>", handled=False)
        goal_id = str(args[1]).strip()
        grant_id = str(args[2]).strip()
        try:
            revoked = GoalArtifactService().revoke_grant(goal_id=goal_id, grant_id=grant_id, revoke_reason="mail_revoke")
        except GoalArtifactServiceError as exc:
            return CommandResult(state, f"mail revoke failed: {exc.reason_code}", handled=False)
        return CommandResult(state.with_updates(status_message=f"mail grant revoked {grant_id}"), json.dumps(revoked, ensure_ascii=False))
    if sub == "context-envelope":
        if len(args) < 2:
            return CommandResult(state, "mail context-envelope <goal-id> [--target cloud_worker|local_worker]", handled=False)
        goal_id = str(args[1]).strip()
        target = "local_worker"
        for idx, token in enumerate(args[2:], start=2):
            if str(token).lower() == "--target" and idx + 1 < len(args):
                target = str(args[idx + 1]).strip()
        envelope = build_mail_context_envelope(goal_id=goal_id, worker_target=target, repo_root=str(repo_root))
        return CommandResult(state.with_updates(status_message=f"mail context-envelope {goal_id} target={target}"), json.dumps(envelope, ensure_ascii=False))
    return CommandResult(
        state,
        "mail | mail account list|status|create|use|disable|delete | mail mailbox <name> | mail open <message-id|uid> | mail load-body [message-id|uid] | mail search <query> | mail filter key=value ... | mail note add <text> | mail link-current-to-goal <goal-id> | mail artifact register-current [--scope ...] | mail attachment list|download <filename>|register <filename> | mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>] | mail grant-current-to-goal <goal-id> [--scope ...] [--confirm-full-body] | mail revoke-grant <goal-id> <grant-id> | mail context-envelope <goal-id> [--target ...] | mail snake-explain | mail scroll <delta>",
        handled=False,
    )
