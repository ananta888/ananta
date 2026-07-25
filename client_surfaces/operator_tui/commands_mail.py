"""Provider-neutral mail command handler for the Ananta operator TUI.

The TUI owns presentation state only. Account persistence, provider routing,
metadata access and capability-gated content access stay behind
``MailApplicationService``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.services.mail_application_service import (
    MailApplicationError,
    MailApplicationService,
    get_mail_application_service,
)
from agent.services.mail_task_service import MailWorkspaceScope
from client_surfaces.operator_tui.models import CommandResult, OperatorState, PanelState


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _mail_repo_root() -> Path:
    return Path.cwd()


def _mail_application(repo_root: Path) -> MailApplicationService:
    return get_mail_application_service(root=repo_root)


def _option(tokens: Sequence[str], name: str) -> str:
    key = f"--{name}"
    for index, token in enumerate(tokens):
        normalized = str(token).strip()
        if normalized.lower() == key and index + 1 < len(tokens):
            return str(tokens[index + 1]).strip()
        if normalized.lower().startswith(f"{key}="):
            return normalized.split("=", 1)[1].strip()
    return ""


def _flag(tokens: Sequence[str], name: str) -> bool:
    return f"--{name}" in {str(token).strip().lower() for token in tokens}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        return dict(mapped) if isinstance(mapped, Mapping) else {}
    return {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if str(key).lower()
            not in {"body", "content", "raw", "data", "credential_ref", "password", "token"}
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return {"content_omitted": True, "size": len(value)}
    return value


def _message_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("message_ref") or {}), **dict(row)}
    protocol = str(source.get("protocol") or "imap").lower()
    mail_ref_id = str(source.get("mail_ref_id") or "").strip()
    ref: dict[str, Any] = {
        "mail_ref_id": mail_ref_id,
        "account_id": str(source.get("account_id") or ""),
        "protocol": protocol,
    }
    thread_ref = str(source.get("thread_ref_id") or "").strip()
    if thread_ref:
        ref["thread_ref_id"] = thread_ref
    message_id = str(source.get("message_id_header") or source.get("message_id") or "").strip()
    if message_id:
        ref["message_id"] = message_id
    # Legacy locators are input compatibility only. JMAP provider locators never
    # cross the surface boundary.
    if protocol == "imap":
        mailbox = str(source.get("mailbox") or "").strip()
        uid = source.get("uid")
        if mailbox:
            ref["mailbox"] = mailbox
        if uid is not None:
            ref["uid"] = uid
    return ref


def _header_meta(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("header_meta") or {}), **dict(row)}
    raw_to = source.get("to_addresses") or source.get("to") or []
    to_addresses = [raw_to] if isinstance(raw_to, str) else list(raw_to)
    header = {
        "subject": str(source.get("subject") or ""),
        "from": str(source.get("from_address") or source.get("from") or ""),
        "to": to_addresses,
        "date": str(source.get("date") or ""),
        "unread": bool(source.get("unread", False)),
        "size": int(source.get("size") or 0),
    }
    message_id = str(source.get("message_id_header") or source.get("message_id") or "").strip()
    if message_id:
        header["message_id"] = message_id
    return header


def _normalize_message(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("message_ref") or {}), **dict(row.get("header_meta") or {}), **dict(row)}
    ref = _message_ref(source)
    return {
        "mail_ref_id": str(ref.get("mail_ref_id") or ""),
        "message_ref": ref,
        "header_meta": _header_meta(source),
        "mailbox_ref_ids": [
            str(item)
            for item in list(source.get("mailbox_ref_ids") or [])
            if str(item).strip()
        ],
        "keywords": dict(source.get("keywords") or {}),
        "stale": bool(source.get("stale", False)),
        "body_scope": "metadata_only",
        "source_ref": str(source.get("source_ref") or ""),
        "attachments": [
            dict(item)
            for item in list(source.get("attachments") or [])
            if isinstance(item, Mapping)
        ],
    }


def _mail_message_key(row: Mapping[str, Any]) -> str:
    ref = _message_ref(row)
    mail_ref_id = str(ref.get("mail_ref_id") or "").strip()
    if mail_ref_id:
        return mail_ref_id
    message_id = str(ref.get("message_id") or "").strip()
    if message_id:
        return message_id
    return f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"


def _mailboxes(row: Mapping[str, Any]) -> set[str]:
    normalized = _normalize_message(row)
    values = {str(item) for item in normalized.get("mailbox_ref_ids") or [] if str(item)}
    legacy = str(dict(normalized.get("message_ref") or {}).get("mailbox") or "").strip()
    if legacy:
        values.add(legacy)
    return values


def _matches_filters(row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    normalized = _normalize_message(row)
    ref = dict(normalized.get("message_ref") or {})
    header = dict(normalized.get("header_meta") or {})
    mailbox = str(filters.get("mailbox") or "")
    if mailbox and mailbox not in _mailboxes(normalized):
        return False
    if filters.get("from") and str(filters["from"]).casefold() not in str(header.get("from") or "").casefold():
        return False
    if filters.get("to") and str(filters["to"]).casefold() not in " ".join(str(item) for item in header.get("to") or []).casefold():
        return False
    if filters.get("subject") and str(filters["subject"]).casefold() not in str(header.get("subject") or "").casefold():
        return False
    if filters.get("unread") is not None and bool(header.get("unread")) is not bool(filters["unread"]):
        return False
    date = str(header.get("date") or "")
    if filters.get("date_from") and date < str(filters["date_from"]):
        return False
    if filters.get("date_to") and date > str(filters["date_to"]):
        return False
    return bool(ref.get("mail_ref_id") or ref.get("uid") is not None)


def _annotate_thread_counts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    normalized = [_normalize_message(row) for row in rows]
    for row in normalized:
        ref = dict(row.get("message_ref") or {})
        thread_ref = str(ref.get("thread_ref_id") or ref.get("mail_ref_id") or "")
        counts[thread_ref] = counts.get(thread_ref, 0) + 1
    for row in normalized:
        ref = dict(row.get("message_ref") or {})
        thread_ref = str(ref.get("thread_ref_id") or ref.get("mail_ref_id") or "")
        row["thread_message_count"] = counts.get(thread_ref, 1)
    return normalized


def _account_status(account: Mapping[str, Any]) -> dict[str, Any]:
    enabled = bool(account.get("enabled", True))
    last_task = dict(account.get("last_task") or {})
    task_status = str(last_task.get("status") or "")
    if not enabled:
        state = "disabled"
        reason_code = "account_disabled"
    elif task_status in {"queued", "pending", "processing", "running"}:
        state = "syncing"
        reason_code = "mail_task_active"
    elif task_status in {"failed", "cancelled"}:
        state = "degraded"
        reason_code = str(last_task.get("reason_code") or f"mail_task_{task_status}")
    elif str(account.get("runtime_state") or "") == "offline":
        state = "offline"
        reason_code = "passive_provider_offline"
    else:
        state = "ready"
        reason_code = "passive_metadata_ready"
    return {
        **dict(account),
        "state": state,
        "reason_code": reason_code,
    }


def _build_mail_payload(*, game: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    application = _mail_application(repo_root)
    accounts = [dict(item) for item in application.list_accounts()]
    selected_account_id = str(game.get("mail_selected_account_id") or "").strip()
    if not selected_account_id and accounts:
        selected_account_id = str(accounts[0].get("account_id") or "")
        game["mail_selected_account_id"] = selected_account_id
    selected_account = next(
        (dict(item) for item in accounts if str(item.get("account_id") or "") == selected_account_id),
        dict(accounts[0]) if accounts else {},
    )

    rows = [dict(item) for item in application.list_message_metadata(account_id=selected_account_id or None)]
    mock_rows = [dict(item) for item in list(game.get("mail_mock_messages") or []) if isinstance(item, Mapping)]
    if mock_rows:
        flattened = [
            {**dict(item.get("message_ref") or {}), **dict(item.get("header_meta") or {}), **item}
            for item in mock_rows
        ]
        rows.extend(application.sanitize_message_metadata_rows(flattened, default_protocol="imap"))
    if selected_account_id:
        rows = [
            row
            for row in rows
            if str(_message_ref(row).get("account_id") or "") == selected_account_id
        ]

    mailbox_set = sorted({mailbox for row in rows for mailbox in _mailboxes(row)})
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
    threaded_rows = _annotate_thread_counts([row for row in rows if _matches_filters(row, query_filters)])
    offset = max(0, int(game.get("mail_list_offset") or 0))
    page_rows = threaded_rows[offset : offset + 20]
    selected_message_key = str(game.get("mail_selected_message_key") or "").strip()
    selected_row = next(
        (row for row in threaded_rows if _mail_message_key(row) == selected_message_key),
        dict(page_rows[0]) if page_rows else {},
    )
    attachments = list(selected_row.get("attachments") or [])
    selected_mail_ref_id = _mail_message_key(selected_row) if selected_row else ""
    if selected_mail_ref_id:
        try:
            attachments = application.attachment_metadata(selected_mail_ref_id)
        except MailApplicationError:
            pass
    selected_detail = {
        "mail_ref_id": selected_mail_ref_id,
        "message_ref": dict(selected_row.get("message_ref") or {}),
        "header_meta": dict(selected_row.get("header_meta") or {}),
        "body_scope": "metadata_only",
        "redaction_status": str(game.get("mail_detail_redaction_status") or "not_required"),
        "body_loaded": bool(game.get("mail_detail_body_loaded", False)),
        "body_text": str(game.get("mail_detail_body") or "")
        if bool(game.get("mail_detail_body_loaded", False))
        else "",
        "attachments": [_json_safe(item) for item in attachments if isinstance(item, Mapping)],
        "attachment_downloaded": _json_safe(dict(game.get("mail_attachment_last_download") or {})),
    }
    current_artifact = dict(game.get("mail_current_artifact") or {})
    artifacts = [dict(item) for item in list(game.get("mail_artifacts") or []) if isinstance(item, Mapping)]
    return {
        "mail_mode": True,
        "accounts": [_account_status(account) for account in accounts],
        "selected_account_id": selected_account_id,
        "selected_account": selected_account,
        "mailboxes": mailbox_set,
        "selected_mailbox": selected_mailbox,
        "filters": filters,
        "list_offset": offset,
        "total_messages": len(threaded_rows),
        "messages": page_rows,
        "selected_message_key": selected_mail_ref_id,
        "selected_detail": selected_detail,
        "last_search_query": str(game.get("mail_last_search_query") or ""),
        "search_result_refs": [str(item) for item in list(game.get("mail_search_result_refs") or []) if str(item).strip()],
        "notes": [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, Mapping)],
        "linked_goal_refs": [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()],
        "account_preview": _json_safe(dict(game.get("mail_account_preview_public") or {})),
        "current_artifact_ref": str(game.get("mail_current_artifact_ref") or ""),
        "current_artifact": _json_safe(current_artifact),
        "artifact_count": len(artifacts),
    }


def _view_result(
    state: OperatorState,
    game: dict[str, Any],
    repo_root: Path,
    status_message: str,
    *,
    output: Mapping[str, Any] | None = None,
) -> CommandResult:
    payload = _build_mail_payload(game=game, repo_root=repo_root)
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = payload
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    rendered = dict(output) if output is not None else payload
    if output is not None:
        rendered.setdefault("payload", payload)
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            section_id="artifacts",
            selected_index=0,
            section_payloads=section_payloads,
            panel_states=panel_states,
            status_message=status_message,
        ),
        json.dumps(_json_safe(rendered), ensure_ascii=False),
    )


def _selected_row(payload: Mapping[str, Any], target: str = "") -> dict[str, Any]:
    rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, Mapping)]
    if target:
        for row in rows:
            ref = _message_ref(row)
            if _mail_message_key(row) == target or str(ref.get("uid") or "") == target:
                return row
    selected = str(payload.get("selected_message_key") or "")
    return next((row for row in rows if _mail_message_key(row) == selected), {})


def _body_text(value: Mapping[str, Any]) -> str:
    for field in ("body_text", "text", "body", "value"):
        candidate = value.get(field)
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, Mapping):
            nested = candidate.get("text") or candidate.get("value")
            if isinstance(nested, str):
                return nested
    return ""


def _authorize_content(
    application: MailApplicationService,
    row: Mapping[str, Any],
    tokens: Sequence[str],
    *,
    release_scope: str,
    confirmation_flag: str,
    explicit_command: bool = False,
) -> Any:
    if not explicit_command and not _flag(tokens, confirmation_flag):
        raise MailApplicationError("mail_content_confirmation_required")
    ref = _message_ref(row)
    mail_ref_id = str(ref.get("mail_ref_id") or "")
    account_id = str(ref.get("account_id") or "")
    if not mail_ref_id or not account_id:
        raise MailApplicationError("mail_message_ref_invalid")
    workspace_id = _option(tokens, "workspace-id") or "operator-tui"
    grant_ref = _option(tokens, "grant-ref") or (
        "operator-confirmation:"
        + hashlib.sha256(f"{workspace_id}:{mail_ref_id}:{release_scope}".encode("utf-8")).hexdigest()[:16]
    )
    return application.authorize_operator_content(
        mail_ref_id=mail_ref_id,
        account_id=account_id,
        workspace_id=workspace_id,
        artifact_ref=f"mail://{mail_ref_id}?scope={release_scope}",
        grant_ref=grant_ref,
        release_scope=release_scope,
        explicit_confirmation=True,
    )


def _extension(application: MailApplicationService, operation: str, **kwargs: Any) -> Any:
    method = getattr(application, operation, None)
    if not callable(method):
        raise MailApplicationError(f"mail_{operation}_unavailable")
    return method(**kwargs)


def _parse_search_filters(query: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
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
                filters["date_from"], filters["date_to"] = value.split("..", 1)
        elif lowered.startswith("unread:"):
            filters["unread"] = token.split(":", 1)[1].lower() in {"1", "true", "yes", "on"}
        else:
            filters["subject"] = f"{filters.get('subject', '')} {token}".strip()
    return filters


def handle_mail_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch ``:mail`` subcommands through the application facade."""
    repo_root = _mail_repo_root()
    application = _mail_application(repo_root)
    game = dict(state.header_logo_game or {})
    if not args:
        return _view_result(state, game, repo_root, "mail view opened")
    sub = str(args[0]).lower()

    if sub == "account":
        usage = (
            "mail account list|status|add|create|preview|discover|confirm|use|disable|delete; "
            "add options: --display-name <name> --username-ref <ref> --credential-ref <ref> "
            "[--account-id <id>] [--protocol auto|jmap|imap] [--session-url <url>]"
        )
        if len(args) < 2:
            return CommandResult(state, usage, handled=False)
        action = str(args[1]).lower()
        if action == "list":
            accounts = application.list_accounts()
            return CommandResult(
                state.with_updates(status_message=f"mail accounts={len(accounts)}"),
                json.dumps({"accounts": _json_safe(accounts)}, ensure_ascii=False),
            )
        if action == "status":
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"mail account status rows={len(payload.get('accounts') or [])}"),
                json.dumps({"accounts": payload.get("accounts") or []}, ensure_ascii=False),
            )
        if action in {"add", "create", "preview"}:
            tokens = list(args[2:])
            if any(
                str(token).strip().lower() in {"--password", "--token"}
                or str(token).strip().lower().startswith(("--password=", "--token="))
                for token in tokens
            ):
                return CommandResult(state, "mail account requires credential_ref, not password/token", handled=False)
            display_name = _option(tokens, "display-name")
            username_ref = _option(tokens, "username-ref") or _option(tokens, "username")
            credential_ref = _option(tokens, "credential-ref")
            host = _option(tokens, "host")
            port_text = _option(tokens, "port")
            session_url = _option(tokens, "session-url")
            protocol = (_option(tokens, "protocol") or ("imap" if host else "auto")).lower()
            sync_policy = _option(tokens, "sync-policy") or "headers_only"
            if not (display_name and username_ref and credential_ref):
                return CommandResult(state, usage, handled=False)
            provider_config: dict[str, Any] = {}
            if session_url:
                provider_config["session_url"] = session_url
            if host:
                provider_config["host"] = host
            if port_text:
                try:
                    provider_config["port"] = int(port_text)
                except ValueError:
                    return CommandResult(state, "mail account --port must be integer", handled=False)
            account_id = _option(tokens, "account-id") or (
                "mail-"
                + hashlib.sha256(f"{display_name}:{username_ref}".encode("utf-8")).hexdigest()[:12]
            )
            try:
                preview = application.preview_account(
                    account_id=account_id,
                    display_name=display_name,
                    requested_protocol=protocol,
                    username_ref=username_ref,
                    credential_ref=credential_ref,
                    sync_policy=sync_policy,
                    provider_config=provider_config,
                )
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail account preview failed: {exc}", handled=False)
            game["mail_account_preview"] = dict(preview.get("draft") or {})
            game["mail_account_preview_public"] = dict(preview.get("account") or {})
            if action == "create" and _flag(tokens, "confirm") and protocol != "auto":
                try:
                    account = application.confirm_account(preview=game["mail_account_preview"])
                except (MailApplicationError, ValueError) as exc:
                    return CommandResult(state, f"mail account confirm failed: {exc}", handled=False)
                game.pop("mail_account_preview", None)
                game.pop("mail_account_preview_public", None)
                return _view_result(
                    state,
                    game,
                    repo_root,
                    f"mail account created {account.get('account_id')}",
                    output={"account": account},
                )
            return _view_result(
                state,
                game,
                repo_root,
                f"mail account preview {account_id}; explicit confirmation required",
                output={"preview": preview.get("account"), "next": "discover" if protocol == "auto" else "confirm"},
            )
        if action == "discover":
            preview = dict(game.get("mail_account_preview") or {})
            if not preview:
                return CommandResult(state, "mail account discover failed: no staged preview", handled=False)
            tokens = list(args[2:])
            workspace_id = _option(tokens, "workspace-id") or "operator-tui"
            tenant_id = _option(tokens, "tenant-id")
            actor_ref = _option(tokens, "actor-ref") or "operator-tui"
            idempotency_key = _option(tokens, "idempotency-key") or (
                "mail-discovery-"
                + hashlib.sha256(json.dumps(preview, sort_keys=True).encode("utf-8")).hexdigest()[:20]
            )
            try:
                task = application.request_discovery(
                    preview=preview,
                    workspace=MailWorkspaceScope(workspace_id=workspace_id, tenant_id=tenant_id),
                    idempotency_key=idempotency_key,
                    actor_ref=actor_ref,
                )
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail account discovery failed: {exc}", handled=False)
            game["mail_account_discovery_task_id"] = str(
                task.get("job_id") or task.get("task_id") or task.get("id") or ""
            )
            return _view_result(
                state,
                game,
                repo_root,
                f"mail account discovery queued {game['mail_account_discovery_task_id']}",
                output={"task": task},
            )
        if action == "confirm":
            preview = dict(game.get("mail_account_preview") or {})
            if not preview:
                return CommandResult(state, "mail account confirm failed: no staged preview", handled=False)
            tokens = list(args[2:])
            resolved_protocol = (_option(tokens, "protocol") or "").lower() or None
            task_id = _option(tokens, "task-id") or str(game.get("mail_account_discovery_task_id") or "") or None
            if str(preview.get("requested_protocol") or "") == "auto" and not task_id:
                return CommandResult(state, "mail account confirm failed: discovery task required for auto", handled=False)
            try:
                account = application.confirm_account(
                    preview=preview,
                    resolved_protocol=resolved_protocol,
                    discovery_task_id=task_id,
                )
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail account confirm failed: {exc}", handled=False)
            game.pop("mail_account_preview", None)
            game.pop("mail_account_preview_public", None)
            game.pop("mail_account_discovery_task_id", None)
            return _view_result(
                state,
                game,
                repo_root,
                f"mail account confirmed {account.get('account_id')}",
                output={"account": account},
            )
        if action == "use":
            if len(args) < 3:
                return CommandResult(state, "mail account use <account-id>", handled=False)
            game["mail_selected_account_id"] = str(args[2]).strip()
            game.pop("mail_selected_mailbox", None)
            game["mail_list_offset"] = 0
            return _view_result(state, game, repo_root, f"mail account {args[2]} selected")
        if action in {"disable", "delete"}:
            if len(args) < 3:
                return CommandResult(state, f"mail account {action} <account-id>", handled=False)
            account_id = str(args[2]).strip()
            try:
                account = (
                    application.disable_account(account_id)
                    if action == "disable"
                    else application.delete_account(account_id)
                )
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail account {action} failed: {exc}", handled=False)
            if action == "delete" and str(game.get("mail_selected_account_id") or "") == account_id:
                game.pop("mail_selected_account_id", None)
                game.pop("mail_selected_mailbox", None)
            return _view_result(
                state,
                game,
                repo_root,
                f"mail account {action}d {account_id}",
                output={"account" if action == "disable" else "deleted_account_id": account if action == "disable" else account_id},
            )
        return CommandResult(state, usage, handled=False)

    if sub == "mailbox":
        if len(args) < 2:
            return CommandResult(state, "mail mailbox <name>", handled=False)
        game["mail_selected_mailbox"] = str(args[1]).strip()
        game["mail_list_offset"] = 0
        return _view_result(state, game, repo_root, f"mail mailbox {args[1]} selected")

    if sub == "scroll":
        if len(args) < 2:
            return CommandResult(state, "mail scroll <delta>", handled=False)
        try:
            delta = int(str(args[1]).strip())
        except ValueError:
            return CommandResult(state, "mail scroll <delta>", handled=False)
        game["mail_list_offset"] = max(0, int(game.get("mail_list_offset") or 0) + delta)
        return _view_result(state, game, repo_root, f"mail scroll offset={game['mail_list_offset']}")

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
        return _view_result(state, game, repo_root, "mail filters updated")

    if sub == "open":
        if len(args) < 2:
            return CommandResult(state, "mail open <mail-ref-id|legacy-uid>", handled=False)
        target = str(args[1]).strip()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload, target)
        if not row:
            return CommandResult(state, "mail open failed: message not found", handled=False)
        game["mail_selected_message_key"] = _mail_message_key(row)
        game["mail_detail_body_loaded"] = False
        game["mail_detail_body"] = ""
        game["mail_detail_redaction_status"] = "not_required"
        return _view_result(state, game, repo_root, f"mail open {_mail_message_key(row)}")

    if sub == "load-body":
        tokens = list(args[1:])
        target = str(tokens[0]).strip() if tokens and not str(tokens[0]).startswith("--") else str(game.get("mail_selected_message_key") or "")
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload, target)
        if not row:
            return CommandResult(state, "mail load-body failed: message not found", handled=False)
        mail_ref_id = _mail_message_key(row)
        try:
            access = _authorize_content(
                application,
                row,
                tokens,
                release_scope="full_body",
                confirmation_flag="confirm-body",
                explicit_command=True,
            )
            loaded = application.load_body(mail_ref_id, access=access)
        except (MailApplicationError, ValueError) as exc:
            return CommandResult(state, f"mail load-body failed: {exc}", handled=False)
        game["mail_selected_message_key"] = mail_ref_id
        game["mail_detail_body_loaded"] = True
        game["mail_detail_body"] = _body_text(_mapping(loaded))
        game["mail_detail_redaction_status"] = "operator_explicit_access"
        return _view_result(
            state,
            game,
            repo_root,
            f"mail body loaded for {mail_ref_id}",
            output={"content_access": {"authorized": True, "mail_ref_id": mail_ref_id, "release_scope": "full_body"}},
        )

    if sub == "attachment":
        if len(args) < 2:
            return CommandResult(state, "mail attachment list|download|register ...", handled=False)
        action = str(args[1]).lower()
        tokens = list(args[2:])
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        message_ref = dict(detail.get("message_ref") or {})
        mail_ref_id = str(message_ref.get("mail_ref_id") or "")
        attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, Mapping)]
        if action == "list":
            return CommandResult(
                state.with_updates(status_message=f"mail attachments={len(attachments)}"),
                json.dumps({"attachments": _json_safe(attachments), "message_ref": message_ref}, ensure_ascii=False),
            )
        if action == "download":
            if not tokens or str(tokens[0]).startswith("--"):
                return CommandResult(state, "mail attachment download <filename|attachment-id> --confirm-attachment", handled=False)
            selector = str(tokens[0]).strip()
            target = next(
                (
                    item
                    for item in attachments
                    if selector
                    in {
                        str(item.get("filename") or ""),
                        str(item.get("attachment_id") or ""),
                        str(item.get("blob_id") or ""),
                    }
                ),
                {},
            )
            if not message_ref or not target:
                return CommandResult(state, "mail attachment download failed: attachment not found", handled=False)
            attachment_id = str(target.get("attachment_id") or target.get("blob_id") or target.get("filename") or "")
            try:
                access = _authorize_content(
                    application,
                    {"message_ref": message_ref},
                    tokens,
                    release_scope="attachment_ref",
                    confirmation_flag="confirm-attachment",
                    explicit_command=True,
                )
                downloaded = application.load_attachment(mail_ref_id, attachment_id, access=access)
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail attachment download failed: {exc}", handled=False)
            summary = _json_safe(_mapping(downloaded))
            game["mail_attachment_last_download"] = summary
            return _view_result(
                state,
                game,
                repo_root,
                f"mail attachment loaded {selector}",
                output={"download": summary},
            )
        if action == "register":
            if not tokens or str(tokens[0]).startswith("--"):
                return CommandResult(state, "mail attachment register <filename|attachment-id>", handled=False)
            selector = str(tokens[0]).strip()
            target = next(
                (item for item in attachments if selector in {str(item.get("filename") or ""), str(item.get("attachment_id") or "")}),
                {},
            )
            if not message_ref or not target:
                return CommandResult(state, "mail attachment register failed: attachment not found", handled=False)
            try:
                artifact_access = _authorize_content(
                    application,
                    {"message_ref": message_ref},
                    tokens,
                    release_scope="attachment_ref",
                    confirmation_flag="confirm-attachment",
                    explicit_command=True,
                )
                artifact = _mapping(
                    _extension(
                        application,
                        "register_artifact",
                        message_ref=message_ref,
                        scope="attachment_ref",
                        redaction_status="not_required",
                        policy_decision_ref="policy:mail:attachment_ref",
                        excerpt=str(target.get("filename") or target.get("attachment_id") or ""),
                        access=artifact_access,
                    )
                )
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail attachment register failed: {exc}", handled=False)
            game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
            game["mail_current_artifact"] = artifact
            game["mail_artifacts"] = [*list(game.get("mail_artifacts") or []), artifact]
            return _view_result(state, game, repo_root, f"mail attachment artifact registered {selector}", output={"artifact": artifact})
        return CommandResult(state, "mail attachment list|download <filename>|register <filename>", handled=False)

    if sub == "export":
        if len(args) < 2 or str(args[1]).lower() != "current":
            return CommandResult(state, "mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]", handled=False)
        tokens = list(args[2:])
        format_name = _option(tokens, "format") or "json"
        include_body = _flag(tokens, "include-body")
        goal_id = _option(tokens, "goal")
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload)
        if not row:
            return CommandResult(state, "mail export failed: no selected message", handled=False)
        body_text = ""
        export_access = None
        if include_body:
            try:
                export_access = _authorize_content(application, row, tokens, release_scope="full_body", confirmation_flag="confirm-body")
                body_text = _body_text(_mapping(application.load_body(_mail_message_key(row), access=export_access)))
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail export failed: {exc}", handled=False)
        try:
            exported = _mapping(
                _extension(
                    application,
                    "export_message",
                    message_ref=_message_ref(row),
                    header_meta=_header_meta(row),
                    body_text=body_text,
                    format_name=format_name,
                    include_body=include_body,
                    access=export_access,
                )
            )
        except (MailApplicationError, ValueError) as exc:
            return CommandResult(state, f"mail export failed: {exc}", handled=False)
        output_artifact: dict[str, Any] = {}
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
            json.dumps({"export": _json_safe(exported), "goal_output_artifact": output_artifact}, ensure_ascii=False),
        )

    if sub == "snake-explain":
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        detail = dict(payload.get("selected_detail") or {})
        try:
            explain = _mapping(
                _extension(
                    application,
                    "explain_for_snake",
                    opened=bool(detail.get("message_ref")),
                    artifact_ref=str(payload.get("current_artifact_ref") or ""),
                    message_ref=dict(detail.get("message_ref") or {}),
                    body_text=str(detail.get("body_text") or ""),
                )
            )
        except (MailApplicationError, ValueError) as exc:
            return CommandResult(state, f"mail snake explain failed: {exc}", handled=False)
        if not bool(explain.get("ok")):
            return CommandResult(state, f"mail snake explain failed: {explain.get('reason_code')}", handled=False)
        return CommandResult(state.with_updates(status_message="mail snake explain ready"), json.dumps(_json_safe(explain), ensure_ascii=False))

    if sub == "search":
        query = " ".join(args[1:]).strip()
        if not query:
            return CommandResult(state, "mail search <query>", handled=False)
        game["mail_filters"] = _parse_search_filters(query)
        game["mail_list_offset"] = 0
        game["mail_last_search_query"] = query
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        refs = [
            f"mail://{_mail_message_key(row)}"
            for row in list(payload.get("messages") or [])
            if isinstance(row, Mapping) and _mail_message_key(row)
        ]
        game["mail_search_result_refs"] = refs
        return _view_result(state, game, repo_root, f"mail search results={len(refs)}")

    if sub == "note":
        if len(args) < 3 or str(args[1]).lower() != "add":
            return CommandResult(state, "mail note add <text>", handled=False)
        text = " ".join(args[2:]).strip()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload)
        if not text or not row:
            return CommandResult(state, "mail note add failed: text and selected message required", handled=False)
        note = {
            "mail_ref_id": _mail_message_key(row),
            "message_ref": _message_ref(row),
            "note": text,
            "created_at": _now_iso(),
        }
        game["mail_notes"] = [*list(game.get("mail_notes") or []), note]
        return _view_result(state, game, repo_root, "mail note added")

    if sub == "link-current-to-goal":
        if len(args) < 2:
            return CommandResult(state, "mail link-current-to-goal <goal-id>", handled=False)
        goal_id = str(args[1]).strip()
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload)
        if not row:
            return CommandResult(state, "mail link failed: no selected message", handled=False)
        ref = _message_ref(row)
        source_ref = (
            f"mail://{ref.get('account_id')}/{ref.get('mailbox')}/{ref.get('uid')}"
            if ref.get("protocol") == "imap"
            and ref.get("mailbox")
            and ref.get("uid") is not None
            else f"mail://{_mail_message_key(row)}"
        )
        entry = f"{goal_id}:{source_ref}"
        links = [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()]
        if entry not in links:
            links.append(entry)
        game["mail_linked_goal_refs"] = links
        return _view_result(state, game, repo_root, f"mail linked to goal {goal_id}")

    if sub in {"artifact", "grant-current-to-goal"}:
        is_grant = sub == "grant-current-to-goal"
        if (not is_grant and (len(args) < 2 or str(args[1]).lower() != "register-current")) or (is_grant and len(args) < 2):
            usage = "mail grant-current-to-goal <goal-id> [--scope metadata_only|excerpt|full_body]" if is_grant else "mail artifact register-current [--scope metadata_only|excerpt|full_body]"
            return CommandResult(state, usage, handled=False)
        goal_id = str(args[1]).strip() if is_grant else ""
        tokens = list(args[2:])
        scope = (_option(tokens, "scope") or "metadata_only").lower()
        if scope == "body_excerpt":
            scope = "excerpt"
        payload = _build_mail_payload(game=game, repo_root=repo_root)
        row = _selected_row(payload)
        if not row:
            return CommandResult(state, f"mail {'grant' if is_grant else 'artifact'} failed: no selected message", handled=False)
        excerpt = ""
        artifact_access = None
        if scope in {"excerpt", "full_body"}:
            confirmation = "confirm-full-body" if scope == "full_body" else "confirm-body"
            release_scope = "full_body" if scope == "full_body" else "body_excerpt"
            try:
                artifact_access = _authorize_content(
                    application,
                    row,
                    tokens,
                    release_scope=release_scope,
                    confirmation_flag=confirmation,
                    explicit_command=True,
                )
                excerpt = _body_text(_mapping(application.load_body(_mail_message_key(row), access=artifact_access)))
                if scope == "excerpt":
                    excerpt = excerpt[:1000]
            except (MailApplicationError, ValueError) as exc:
                return CommandResult(state, f"mail artifact content failed: {exc}", handled=False)
        try:
            artifact = _mapping(
                _extension(
                    application,
                    "register_artifact",
                    message_ref=_message_ref(row),
                    scope=scope,
                    redaction_status="operator_explicit_access" if excerpt else "not_required",
                    policy_decision_ref=f"policy:mail:{scope}",
                    excerpt=excerpt,
                    access=artifact_access,
                )
            )
        except (MailApplicationError, ValueError) as exc:
            return CommandResult(state, f"mail artifact failed: {exc}", handled=False)
        game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
        game["mail_current_artifact"] = artifact
        game["mail_artifacts"] = [*list(game.get("mail_artifacts") or []), artifact]
        if not is_grant:
            return _view_result(state, game, repo_root, f"mail artifact registered {artifact.get('artifact_ref')}", output={"artifact": artifact})
        artifact_ref = str(artifact.get("artifact_ref") or "")
        grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{scope}'.encode('utf-8')).hexdigest()[:10]}"
        grant_payload = {
            "schema": "source_artifact_grant.v1",
            "grant_id": grant_id,
            "goal_id": goal_id,
            "artifact_ref": artifact_ref,
            "granted_by": "operator_tui_mail",
            "granted_at": _now_iso(),
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": f"policy:mail:{scope}",
        }
        try:
            created = GoalArtifactService().create_grant(goal_id=goal_id, grant=grant_payload)
        except GoalArtifactServiceError as exc:
            return CommandResult(state, f"mail grant failed: {exc.reason_code}", handled=False)
        return _view_result(state, game, repo_root, f"mail granted to goal {goal_id}", output={"grant": created, "artifact": artifact})

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
        target = _option(args[2:], "target") or "local_worker"
        try:
            envelope = _mapping(
                _extension(
                    application,
                    "build_context_envelope",
                    goal_id=goal_id,
                    worker_target=target,
                )
            )
        except (MailApplicationError, ValueError) as exc:
            return CommandResult(state, f"mail context-envelope failed: {exc}", handled=False)
        return CommandResult(state.with_updates(status_message=f"mail context-envelope {goal_id} target={target}"), json.dumps(_json_safe(envelope), ensure_ascii=False))

    return CommandResult(
        state,
        "mail | mail account list|status|add|create|preview|discover|confirm|use|disable|delete | mail mailbox <name> | mail open <mail-ref-id|legacy-uid> | mail load-body [mail-ref-id] --confirm-body | mail search <query> | mail filter key=value ... | mail note add <text> | mail link-current-to-goal <goal-id> | mail artifact register-current [--scope ...] | mail attachment list|download <filename> --confirm-attachment|register <filename> | mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>] | mail grant-current-to-goal <goal-id> [--scope ...] | mail revoke-grant <goal-id> <grant-id> | mail context-envelope <goal-id> [--target ...] | mail snake-explain | mail scroll <delta>",
        handled=False,
    )
