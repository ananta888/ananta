"""Shared building blocks of the ``:mail`` operator TUI commands.

Option parsing, the mail view payload projection, content authorization and
the per-invocation :class:`MailCommandContext` live here so that the
subcommand handler modules (``commands_mail``, ``commands_mail_account`` and
``commands_mail_content``) depend on one small support module instead of on
each other.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.mail_application_service import (
    MailApplicationError,
    MailApplicationService,
    get_mail_application_service,
)
from client_surfaces.operator_tui.mail_message_projection import account_status as _account_status
from client_surfaces.operator_tui.mail_message_projection import annotate_thread_counts as _annotate_thread_counts
from client_surfaces.operator_tui.mail_message_projection import json_safe as _json_safe
from client_surfaces.operator_tui.mail_message_projection import mail_message_key as _mail_message_key
from client_surfaces.operator_tui.mail_message_projection import mailboxes as _mailboxes
from client_surfaces.operator_tui.mail_message_projection import matches_filters as _matches_filters
from client_surfaces.operator_tui.mail_message_projection import message_ref as _message_ref
from client_surfaces.operator_tui.models import CommandResult, OperatorState, PanelState


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def mail_repo_root() -> Path:
    return Path.cwd()


def mail_application(repo_root: Path) -> MailApplicationService:
    return get_mail_application_service(root=repo_root)


def option(tokens: Sequence[str], name: str) -> str:
    key = f"--{name}"
    for index, token in enumerate(tokens):
        normalized = str(token).strip()
        if normalized.lower() == key and index + 1 < len(tokens):
            return str(tokens[index + 1]).strip()
        if normalized.lower().startswith(f"{key}="):
            return normalized.split("=", 1)[1].strip()
    return ""


def flag(tokens: Sequence[str], name: str) -> bool:
    return f"--{name}" in {str(token).strip().lower() for token in tokens}


def as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        mapped = to_dict()
        return dict(mapped) if isinstance(mapped, Mapping) else {}
    return {}


def build_mail_payload(*, game: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    application = mail_application(repo_root)
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
    selected_message = next(
        (row for row in threaded_rows if _mail_message_key(row) == selected_message_key),
        dict(page_rows[0]) if page_rows else {},
    )
    attachments = list(selected_message.get("attachments") or [])
    selected_mail_ref_id = _mail_message_key(selected_message) if selected_message else ""
    if selected_mail_ref_id:
        try:
            attachments = application.attachment_metadata(selected_mail_ref_id)
        except MailApplicationError:
            pass
    selected_detail = {
        "mail_ref_id": selected_mail_ref_id,
        "message_ref": dict(selected_message.get("message_ref") or {}),
        "header_meta": dict(selected_message.get("header_meta") or {}),
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
        "search_result_refs": [
            str(item) for item in list(game.get("mail_search_result_refs") or []) if str(item).strip()
        ],
        "notes": [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, Mapping)],
        "linked_goal_refs": [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()],
        "account_preview": _json_safe(dict(game.get("mail_account_preview_public") or {})),
        "current_artifact_ref": str(game.get("mail_current_artifact_ref") or ""),
        "current_artifact": _json_safe(current_artifact),
        "artifact_count": len(artifacts),
    }


def view_result(
    state: OperatorState,
    game: dict[str, Any],
    repo_root: Path,
    status_message: str,
    *,
    output: Mapping[str, Any] | None = None,
) -> CommandResult:
    payload = build_mail_payload(game=game, repo_root=repo_root)
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


def selected_row(payload: Mapping[str, Any], target: str = "") -> dict[str, Any]:
    rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, Mapping)]
    if target:
        for row in rows:
            ref = _message_ref(row)
            if _mail_message_key(row) == target or str(ref.get("uid") or "") == target:
                return row
    selected = str(payload.get("selected_message_key") or "")
    return next((row for row in rows if _mail_message_key(row) == selected), {})


def authorize_content(
    application: MailApplicationService,
    row: Mapping[str, Any],
    tokens: Sequence[str],
    *,
    release_scope: str,
    confirmation_flag: str,
    explicit_command: bool = False,
) -> Any:
    if not explicit_command and not flag(tokens, confirmation_flag):
        raise MailApplicationError("mail_content_confirmation_required")
    ref = _message_ref(row)
    mail_ref_id = str(ref.get("mail_ref_id") or "")
    account_id = str(ref.get("account_id") or "")
    if not mail_ref_id or not account_id:
        raise MailApplicationError("mail_message_ref_invalid")
    workspace_id = option(tokens, "workspace-id") or "operator-tui"
    grant_ref = option(tokens, "grant-ref") or (
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


def call_extension(application: MailApplicationService, operation: str, **kwargs: Any) -> Any:
    method = getattr(application, operation, None)
    if not callable(method):
        raise MailApplicationError(f"mail_{operation}_unavailable")
    return method(**kwargs)


@dataclass
class MailCommandContext:
    """One ``:mail`` invocation: its arguments, the TUI state and the mail facade."""

    args: list[str]
    state: OperatorState
    game: dict[str, Any]
    repo_root: Path
    application: MailApplicationService

    def fail(self, message: str) -> CommandResult:
        return CommandResult(self.state, message, handled=False)

    def view(self, status_message: str, *, output: Mapping[str, Any] | None = None) -> CommandResult:
        return view_result(self.state, self.game, self.repo_root, status_message, output=output)

    def payload(self) -> dict[str, Any]:
        return build_mail_payload(game=self.game, repo_root=self.repo_root)

    def reply(self, status_message: str, output: Any) -> CommandResult:
        return CommandResult(
            self.state.with_updates(status_message=status_message),
            json.dumps(output, ensure_ascii=False),
        )
