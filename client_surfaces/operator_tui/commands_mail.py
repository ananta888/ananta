"""Provider-neutral mail command handler for the Ananta operator TUI.

The TUI owns presentation state only. Account persistence, provider routing,
metadata access and capability-gated content access stay behind
``MailApplicationService``.

``handle_mail_command`` is a thin dispatcher over :data:`MAIL_SUBCOMMANDS`.
The mail view navigation subcommands live in this module; account management
is in ``commands_mail_account`` and the content-releasing subcommands are in
``commands_mail_content``. Shared parsing and projection helpers are in
``_mail_command_support``.
"""
from __future__ import annotations

from typing import Callable, Mapping

from agent.services.mail_application_service import MailApplicationError
from client_surfaces.operator_tui._mail_command_support import (
    MailCommandContext,
    as_mapping,
    authorize_content,
    mail_application,
    mail_repo_root,
    now_iso,
    selected_row,
    view_result,
)
from client_surfaces.operator_tui.commands_mail_account import handle_mail_account_command
from client_surfaces.operator_tui.commands_mail_content import (
    handle_mail_artifact_or_grant,
    handle_mail_attachment,
    handle_mail_context_envelope,
    handle_mail_export,
    handle_mail_revoke_grant,
    handle_mail_snake_explain,
)
from client_surfaces.operator_tui.mail_message_projection import body_text as _body_text
from client_surfaces.operator_tui.mail_message_projection import mail_message_key as _mail_message_key
from client_surfaces.operator_tui.mail_message_projection import message_ref as _message_ref
from client_surfaces.operator_tui.mail_message_projection import parse_search_filters as _parse_search_filters
from client_surfaces.operator_tui.models import CommandResult, OperatorState

MailSubcommandHandler = Callable[[MailCommandContext], CommandResult]

MAIL_USAGE = (
    "mail | mail account list|status|add|create|preview|discover|confirm|use|disable|delete | mail mailbox <name> | "
    "mail open <mail-ref-id|legacy-uid> | mail load-body [mail-ref-id] --confirm-body | mail search <query> | "
    "mail filter key=value ... | mail note add <text> | mail link-current-to-goal <goal-id> | "
    "mail artifact register-current [--scope ...] | "
    "mail attachment list|download <filename> --confirm-attachment|register <filename> | "
    "mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>] | "
    "mail grant-current-to-goal <goal-id> [--scope ...] | mail revoke-grant <goal-id> <grant-id> | "
    "mail context-envelope <goal-id> [--target ...] | mail snake-explain | mail scroll <delta>"
)

_FILTER_TEXT_KEYS = frozenset({"from", "subject", "mailbox", "to", "date_from", "date_to"})


def _select_mailbox(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail mailbox <name>")
    ctx.game["mail_selected_mailbox"] = str(ctx.args[1]).strip()
    ctx.game["mail_list_offset"] = 0
    return ctx.view(f"mail mailbox {ctx.args[1]} selected")


def _scroll(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail scroll <delta>")
    try:
        delta = int(str(ctx.args[1]).strip())
    except ValueError:
        return ctx.fail("mail scroll <delta>")
    ctx.game["mail_list_offset"] = max(0, int(ctx.game.get("mail_list_offset") or 0) + delta)
    return ctx.view(f"mail scroll offset={ctx.game['mail_list_offset']}")


def _apply_filter_token(filters: dict, token: str) -> None:
    if "=" not in token:
        return
    key, value = str(token).split("=", 1)
    normalized_key = key.strip().lower()
    normalized_value = value.strip()
    if normalized_key == "unread":
        filters["unread"] = normalized_value.lower() in {"1", "true", "yes", "on"}
    elif normalized_key in _FILTER_TEXT_KEYS:
        filters[normalized_key] = normalized_value


def _filter(ctx: MailCommandContext) -> CommandResult:
    filters = dict(ctx.game.get("mail_filters") or {})
    for token in ctx.args[1:]:
        _apply_filter_token(filters, token)
    ctx.game["mail_filters"] = filters
    ctx.game["mail_list_offset"] = 0
    return ctx.view("mail filters updated")


def _open_message(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail open <mail-ref-id|legacy-uid>")
    target = str(ctx.args[1]).strip()
    row = selected_row(ctx.payload(), target)
    if not row:
        return ctx.fail("mail open failed: message not found")
    ctx.game["mail_selected_message_key"] = _mail_message_key(row)
    ctx.game["mail_detail_body_loaded"] = False
    ctx.game["mail_detail_body"] = ""
    ctx.game["mail_detail_redaction_status"] = "not_required"
    return ctx.view(f"mail open {_mail_message_key(row)}")


def _load_body(ctx: MailCommandContext) -> CommandResult:
    tokens = list(ctx.args[1:])
    if tokens and not str(tokens[0]).startswith("--"):
        target = str(tokens[0]).strip()
    else:
        target = str(ctx.game.get("mail_selected_message_key") or "")
    row = selected_row(ctx.payload(), target)
    if not row:
        return ctx.fail("mail load-body failed: message not found")
    mail_ref_id = _mail_message_key(row)
    try:
        access = authorize_content(
            ctx.application,
            row,
            tokens,
            release_scope="full_body",
            confirmation_flag="confirm-body",
            explicit_command=True,
        )
        loaded = ctx.application.load_body(mail_ref_id, access=access)
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail load-body failed: {exc}")
    ctx.game["mail_selected_message_key"] = mail_ref_id
    ctx.game["mail_detail_body_loaded"] = True
    ctx.game["mail_detail_body"] = _body_text(as_mapping(loaded))
    ctx.game["mail_detail_redaction_status"] = "operator_explicit_access"
    return ctx.view(
        f"mail body loaded for {mail_ref_id}",
        output={"content_access": {"authorized": True, "mail_ref_id": mail_ref_id, "release_scope": "full_body"}},
    )


def _search(ctx: MailCommandContext) -> CommandResult:
    query = " ".join(ctx.args[1:]).strip()
    if not query:
        return ctx.fail("mail search <query>")
    ctx.game["mail_filters"] = _parse_search_filters(query)
    ctx.game["mail_list_offset"] = 0
    ctx.game["mail_last_search_query"] = query
    refs = [
        f"mail://{_mail_message_key(row)}"
        for row in list(ctx.payload().get("messages") or [])
        if isinstance(row, Mapping) and _mail_message_key(row)
    ]
    ctx.game["mail_search_result_refs"] = refs
    return ctx.view(f"mail search results={len(refs)}")


def _add_note(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 3 or str(ctx.args[1]).lower() != "add":
        return ctx.fail("mail note add <text>")
    text = " ".join(ctx.args[2:]).strip()
    row = selected_row(ctx.payload())
    if not text or not row:
        return ctx.fail("mail note add failed: text and selected message required")
    note = {
        "mail_ref_id": _mail_message_key(row),
        "message_ref": _message_ref(row),
        "note": text,
        "created_at": now_iso(),
    }
    ctx.game["mail_notes"] = [*list(ctx.game.get("mail_notes") or []), note]
    return ctx.view("mail note added")


def _goal_source_ref(row: Mapping) -> str:
    ref = _message_ref(row)
    if ref.get("protocol") == "imap" and ref.get("mailbox") and ref.get("uid") is not None:
        return f"mail://{ref.get('account_id')}/{ref.get('mailbox')}/{ref.get('uid')}"
    return f"mail://{_mail_message_key(row)}"


def _link_current_to_goal(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail link-current-to-goal <goal-id>")
    goal_id = str(ctx.args[1]).strip()
    row = selected_row(ctx.payload())
    if not row:
        return ctx.fail("mail link failed: no selected message")
    entry = f"{goal_id}:{_goal_source_ref(row)}"
    links = [str(item) for item in list(ctx.game.get("mail_linked_goal_refs") or []) if str(item).strip()]
    if entry not in links:
        links.append(entry)
    ctx.game["mail_linked_goal_refs"] = links
    return ctx.view(f"mail linked to goal {goal_id}")


MAIL_SUBCOMMANDS: dict[str, MailSubcommandHandler] = {
    "account": handle_mail_account_command,
    "mailbox": _select_mailbox,
    "scroll": _scroll,
    "filter": _filter,
    "open": _open_message,
    "load-body": _load_body,
    "attachment": handle_mail_attachment,
    "export": handle_mail_export,
    "snake-explain": handle_mail_snake_explain,
    "search": _search,
    "note": _add_note,
    "link-current-to-goal": _link_current_to_goal,
    "artifact": handle_mail_artifact_or_grant,
    "grant-current-to-goal": handle_mail_artifact_or_grant,
    "revoke-grant": handle_mail_revoke_grant,
    "context-envelope": handle_mail_context_envelope,
}


def handle_mail_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch ``:mail`` subcommands through the application facade."""
    repo_root = mail_repo_root()
    application = mail_application(repo_root)
    game = dict(state.header_logo_game or {})
    if not args:
        return view_result(state, game, repo_root, "mail view opened")
    handler = MAIL_SUBCOMMANDS.get(str(args[0]).lower())
    if handler is None:
        return CommandResult(state, MAIL_USAGE, handled=False)
    ctx = MailCommandContext(args=args, state=state, game=game, repo_root=repo_root, application=application)
    return handler(ctx)
