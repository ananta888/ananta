"""``:mail account ...`` subcommands of the operator TUI.

Each account action is one small handler registered in
:data:`MAIL_ACCOUNT_ACTIONS`; :func:`handle_mail_account_command` only looks
the action up. Account persistence and provider discovery stay behind
``MailApplicationService``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from agent.services.mail_application_service import MailApplicationError
from agent.services.mail_task_service import MailWorkspaceScope
from client_surfaces.operator_tui._mail_command_support import MailCommandContext, flag, option
from client_surfaces.operator_tui.mail_message_projection import json_safe as _json_safe
from client_surfaces.operator_tui.models import CommandResult

MAIL_ACCOUNT_USAGE = (
    "mail account list|status|add|create|preview|discover|confirm|use|disable|delete; "
    "add options: --display-name <name> --username-ref <ref> --credential-ref <ref> "
    "[--account-id <id>] [--protocol auto|jmap|imap] [--session-url <url>]"
)

AccountActionHandler = Callable[[MailCommandContext, str], CommandResult]


def _list_accounts(ctx: MailCommandContext, action: str) -> CommandResult:
    accounts = ctx.application.list_accounts()
    return CommandResult(
        ctx.state.with_updates(status_message=f"mail accounts={len(accounts)}"),
        json.dumps({"accounts": _json_safe(accounts)}, ensure_ascii=False),
    )


def _account_status(ctx: MailCommandContext, action: str) -> CommandResult:
    payload = ctx.payload()
    return CommandResult(
        ctx.state.with_updates(
            header_logo_game=ctx.game,
            status_message=f"mail account status rows={len(payload.get('accounts') or [])}",
        ),
        json.dumps({"accounts": payload.get("accounts") or []}, ensure_ascii=False),
    )


def _mentions_raw_secret(tokens: Sequence[str]) -> bool:
    return any(
        str(token).strip().lower() in {"--password", "--token"}
        or str(token).strip().lower().startswith(("--password=", "--token="))
        for token in tokens
    )


def _provider_config(tokens: Sequence[str]) -> dict[str, Any]:
    """Provider settings from the options; raises ValueError for a non-integer port."""
    provider_config: dict[str, Any] = {}
    session_url = option(tokens, "session-url")
    host = option(tokens, "host")
    port_text = option(tokens, "port")
    if session_url:
        provider_config["session_url"] = session_url
    if host:
        provider_config["host"] = host
    if port_text:
        provider_config["port"] = int(port_text)
    return provider_config


def _confirm_created_account(ctx: MailCommandContext) -> CommandResult:
    try:
        account = ctx.application.confirm_account(preview=ctx.game["mail_account_preview"])
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail account confirm failed: {exc}")
    ctx.game.pop("mail_account_preview", None)
    ctx.game.pop("mail_account_preview_public", None)
    return ctx.view(f"mail account created {account.get('account_id')}", output={"account": account})


def _preview_account(ctx: MailCommandContext, action: str) -> CommandResult:
    """``add``, ``create`` and ``preview``: stage an account draft, optionally confirming it."""
    tokens = list(ctx.args[2:])
    if _mentions_raw_secret(tokens):
        return ctx.fail("mail account requires credential_ref, not password/token")
    display_name = option(tokens, "display-name")
    username_ref = option(tokens, "username-ref") or option(tokens, "username")
    credential_ref = option(tokens, "credential-ref")
    protocol = (option(tokens, "protocol") or ("imap" if option(tokens, "host") else "auto")).lower()
    sync_policy = option(tokens, "sync-policy") or "headers_only"
    if not (display_name and username_ref and credential_ref):
        return ctx.fail(MAIL_ACCOUNT_USAGE)
    try:
        provider_config = _provider_config(tokens)
    except ValueError:
        return ctx.fail("mail account --port must be integer")
    account_id = option(tokens, "account-id") or (
        "mail-" + hashlib.sha256(f"{display_name}:{username_ref}".encode("utf-8")).hexdigest()[:12]
    )
    try:
        preview = ctx.application.preview_account(
            account_id=account_id,
            display_name=display_name,
            requested_protocol=protocol,
            username_ref=username_ref,
            credential_ref=credential_ref,
            sync_policy=sync_policy,
            provider_config=provider_config,
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail account preview failed: {exc}")
    ctx.game["mail_account_preview"] = dict(preview.get("draft") or {})
    ctx.game["mail_account_preview_public"] = dict(preview.get("account") or {})
    if action == "create" and flag(tokens, "confirm") and protocol != "auto":
        return _confirm_created_account(ctx)
    return ctx.view(
        f"mail account preview {account_id}; explicit confirmation required",
        output={"preview": preview.get("account"), "next": "discover" if protocol == "auto" else "confirm"},
    )


def _discovery_idempotency_key(tokens: Sequence[str], preview: Mapping[str, Any]) -> str:
    return option(tokens, "idempotency-key") or (
        "mail-discovery-" + hashlib.sha256(json.dumps(preview, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    )


def _discover_account(ctx: MailCommandContext, action: str) -> CommandResult:
    preview = dict(ctx.game.get("mail_account_preview") or {})
    if not preview:
        return ctx.fail("mail account discover failed: no staged preview")
    tokens = list(ctx.args[2:])
    workspace_id = option(tokens, "workspace-id") or "operator-tui"
    tenant_id = option(tokens, "tenant-id")
    actor_ref = option(tokens, "actor-ref") or "operator-tui"
    try:
        task = ctx.application.request_discovery(
            preview=preview,
            workspace=MailWorkspaceScope(workspace_id=workspace_id, tenant_id=tenant_id),
            idempotency_key=_discovery_idempotency_key(tokens, preview),
            actor_ref=actor_ref,
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail account discovery failed: {exc}")
    ctx.game["mail_account_discovery_task_id"] = str(task.get("job_id") or task.get("task_id") or task.get("id") or "")
    return ctx.view(
        f"mail account discovery queued {ctx.game['mail_account_discovery_task_id']}",
        output={"task": task},
    )


def _confirm_account(ctx: MailCommandContext, action: str) -> CommandResult:
    preview = dict(ctx.game.get("mail_account_preview") or {})
    if not preview:
        return ctx.fail("mail account confirm failed: no staged preview")
    tokens = list(ctx.args[2:])
    resolved_protocol = (option(tokens, "protocol") or "").lower() or None
    task_id = option(tokens, "task-id") or str(ctx.game.get("mail_account_discovery_task_id") or "") or None
    if str(preview.get("requested_protocol") or "") == "auto" and not task_id:
        return ctx.fail("mail account confirm failed: discovery task required for auto")
    try:
        account = ctx.application.confirm_account(
            preview=preview,
            resolved_protocol=resolved_protocol,
            discovery_task_id=task_id,
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail account confirm failed: {exc}")
    for key in ("mail_account_preview", "mail_account_preview_public", "mail_account_discovery_task_id"):
        ctx.game.pop(key, None)
    return ctx.view(f"mail account confirmed {account.get('account_id')}", output={"account": account})


def _use_account(ctx: MailCommandContext, action: str) -> CommandResult:
    if len(ctx.args) < 3:
        return ctx.fail("mail account use <account-id>")
    ctx.game["mail_selected_account_id"] = str(ctx.args[2]).strip()
    ctx.game.pop("mail_selected_mailbox", None)
    ctx.game["mail_list_offset"] = 0
    return ctx.view(f"mail account {ctx.args[2]} selected")


def _retire_account(ctx: MailCommandContext, action: str) -> CommandResult:
    """``disable`` and ``delete``."""
    if len(ctx.args) < 3:
        return ctx.fail(f"mail account {action} <account-id>")
    account_id = str(ctx.args[2]).strip()
    try:
        account = (
            ctx.application.disable_account(account_id)
            if action == "disable"
            else ctx.application.delete_account(account_id)
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail account {action} failed: {exc}")
    if action == "delete" and str(ctx.game.get("mail_selected_account_id") or "") == account_id:
        ctx.game.pop("mail_selected_account_id", None)
        ctx.game.pop("mail_selected_mailbox", None)
    output = {"account": account} if action == "disable" else {"deleted_account_id": account_id}
    return ctx.view(f"mail account {action}d {account_id}", output=output)


MAIL_ACCOUNT_ACTIONS: dict[str, AccountActionHandler] = {
    "list": _list_accounts,
    "status": _account_status,
    "add": _preview_account,
    "create": _preview_account,
    "preview": _preview_account,
    "discover": _discover_account,
    "confirm": _confirm_account,
    "use": _use_account,
    "disable": _retire_account,
    "delete": _retire_account,
}


def handle_mail_account_command(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return CommandResult(ctx.state, MAIL_ACCOUNT_USAGE, handled=False)
    action = str(ctx.args[1]).lower()
    handler = MAIL_ACCOUNT_ACTIONS.get(action)
    if handler is None:
        return CommandResult(ctx.state, MAIL_ACCOUNT_USAGE, handled=False)
    return handler(ctx, action)
