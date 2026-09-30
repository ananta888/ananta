"""Content-releasing ``:mail`` subcommands of the operator TUI.

Attachments, exports, artifact registration, goal grants and context
envelopes release message content beyond metadata. Each subcommand is one
handler; every content release still goes through the capability-gated
``authorize_content`` of the shared support module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.services.mail_application_service import MailApplicationError
from client_surfaces.operator_tui._mail_command_support import (
    MailCommandContext,
    as_mapping,
    authorize_content,
    call_extension,
    flag,
    now_iso,
    option,
    selected_row,
)
from client_surfaces.operator_tui.mail_message_projection import body_text as _body_text
from client_surfaces.operator_tui.mail_message_projection import header_meta as _header_meta
from client_surfaces.operator_tui.mail_message_projection import json_safe as _json_safe
from client_surfaces.operator_tui.mail_message_projection import mail_message_key as _mail_message_key
from client_surfaces.operator_tui.mail_message_projection import message_ref as _message_ref
from client_surfaces.operator_tui.models import CommandResult

# --- attachments -----------------------------------------------------------------


def _find_attachment(attachments: Sequence[Mapping[str, Any]], selector: str, keys: Sequence[str]) -> Mapping[str, Any]:
    return next((item for item in attachments if selector in {str(item.get(key) or "") for key in keys}), {})


def _record_current_artifact(ctx: MailCommandContext, artifact: dict[str, Any]) -> None:
    ctx.game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
    ctx.game["mail_current_artifact"] = artifact
    ctx.game["mail_artifacts"] = [*list(ctx.game.get("mail_artifacts") or []), artifact]


def _authorize_attachment(ctx: MailCommandContext, message_ref: Mapping[str, Any], tokens: Sequence[str]) -> Any:
    return authorize_content(
        ctx.application,
        {"message_ref": message_ref},
        tokens,
        release_scope="attachment_ref",
        confirmation_flag="confirm-attachment",
        explicit_command=True,
    )


def _download_attachment(
    ctx: MailCommandContext,
    tokens: Sequence[str],
    message_ref: Mapping[str, Any],
    attachments: Sequence[Mapping[str, Any]],
) -> CommandResult:
    if not tokens or str(tokens[0]).startswith("--"):
        return ctx.fail("mail attachment download <filename|attachment-id> --confirm-attachment")
    selector = str(tokens[0]).strip()
    target = _find_attachment(attachments, selector, ("filename", "attachment_id", "blob_id"))
    if not message_ref or not target:
        return ctx.fail("mail attachment download failed: attachment not found")
    attachment_id = str(target.get("attachment_id") or target.get("blob_id") or target.get("filename") or "")
    mail_ref_id = str(message_ref.get("mail_ref_id") or "")
    try:
        access = _authorize_attachment(ctx, message_ref, tokens)
        downloaded = ctx.application.load_attachment(mail_ref_id, attachment_id, access=access)
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail attachment download failed: {exc}")
    summary = _json_safe(as_mapping(downloaded))
    ctx.game["mail_attachment_last_download"] = summary
    return ctx.view(f"mail attachment loaded {selector}", output={"download": summary})


def _register_attachment(
    ctx: MailCommandContext,
    tokens: Sequence[str],
    message_ref: Mapping[str, Any],
    attachments: Sequence[Mapping[str, Any]],
) -> CommandResult:
    if not tokens or str(tokens[0]).startswith("--"):
        return ctx.fail("mail attachment register <filename|attachment-id>")
    selector = str(tokens[0]).strip()
    target = _find_attachment(attachments, selector, ("filename", "attachment_id"))
    if not message_ref or not target:
        return ctx.fail("mail attachment register failed: attachment not found")
    try:
        artifact_access = _authorize_attachment(ctx, message_ref, tokens)
        artifact = as_mapping(
            call_extension(
                ctx.application,
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
        return ctx.fail(f"mail attachment register failed: {exc}")
    _record_current_artifact(ctx, artifact)
    return ctx.view(f"mail attachment artifact registered {selector}", output={"artifact": artifact})


def handle_mail_attachment(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail attachment list|download|register ...")
    action = str(ctx.args[1]).lower()
    tokens = list(ctx.args[2:])
    detail = dict(ctx.payload().get("selected_detail") or {})
    message_ref = dict(detail.get("message_ref") or {})
    attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, Mapping)]
    if action == "list":
        return CommandResult(
            ctx.state.with_updates(status_message=f"mail attachments={len(attachments)}"),
            json.dumps({"attachments": _json_safe(attachments), "message_ref": message_ref}, ensure_ascii=False),
        )
    if action == "download":
        return _download_attachment(ctx, tokens, message_ref, attachments)
    if action == "register":
        return _register_attachment(ctx, tokens, message_ref, attachments)
    return ctx.fail("mail attachment list|download <filename>|register <filename>")


# --- export ------------------------------------------------------------------------


def _record_export_goal_artifact(goal_id: str, exported: Mapping[str, Any]) -> dict[str, Any]:
    export_hash = hashlib.sha1(str(exported.get("export_ref")).encode("utf-8")).hexdigest()[:12]
    return GoalArtifactService().record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": f"mail-export-{export_hash}",
            "goal_id": goal_id,
            "artifact_type": "file",
            "created_at": now_iso(),
            "artifact_ref": str(exported.get("export_ref") or ""),
            "content_hash": str(exported.get("sha256") or ""),
            "status": "created",
            "provenance_summary": "mail export from operator_tui",
            "provenance_kind": "manual",
        },
    )


def handle_mail_export(ctx: MailCommandContext) -> CommandResult:
    args = ctx.args
    if len(args) < 2 or str(args[1]).lower() != "current":
        return ctx.fail(
            "mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]"
        )
    tokens = list(args[2:])
    format_name = option(tokens, "format") or "json"
    include_body = flag(tokens, "include-body")
    goal_id = option(tokens, "goal")
    row = selected_row(ctx.payload())
    if not row:
        return ctx.fail("mail export failed: no selected message")
    body_text = ""
    export_access = None
    if include_body:
        try:
            export_access = authorize_content(
                ctx.application, row, tokens, release_scope="full_body", confirmation_flag="confirm-body"
            )
            body_text = _body_text(as_mapping(ctx.application.load_body(_mail_message_key(row), access=export_access)))
        except (MailApplicationError, ValueError) as exc:
            return ctx.fail(f"mail export failed: {exc}")
    try:
        exported = as_mapping(
            call_extension(
                ctx.application,
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
        return ctx.fail(f"mail export failed: {exc}")
    output_artifact: dict[str, Any] = {}
    if goal_id:
        try:
            output_artifact = _record_export_goal_artifact(goal_id, exported)
        except GoalArtifactServiceError as exc:
            return ctx.fail(f"mail export goal artifact failed: {exc.reason_code}")
    return ctx.reply(
        f"mail export {format_name}",
        {"export": _json_safe(exported), "goal_output_artifact": output_artifact},
    )


# --- snake explain ----------------------------------------------------------------


def handle_mail_snake_explain(ctx: MailCommandContext) -> CommandResult:
    payload = ctx.payload()
    detail = dict(payload.get("selected_detail") or {})
    try:
        explain = as_mapping(
            call_extension(
                ctx.application,
                "explain_for_snake",
                opened=bool(detail.get("message_ref")),
                artifact_ref=str(payload.get("current_artifact_ref") or ""),
                message_ref=dict(detail.get("message_ref") or {}),
                body_text=str(detail.get("body_text") or ""),
            )
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail snake explain failed: {exc}")
    if not bool(explain.get("ok")):
        return ctx.fail(f"mail snake explain failed: {explain.get('reason_code')}")
    return ctx.reply("mail snake explain ready", _json_safe(explain))


# --- artifact registration and goal grants ------------------------------------------


def _artifact_usage_error(ctx: MailCommandContext, is_grant: bool) -> CommandResult | None:
    args = ctx.args
    if is_grant and len(args) < 2:
        return ctx.fail("mail grant-current-to-goal <goal-id> [--scope metadata_only|excerpt|full_body]")
    if not is_grant and (len(args) < 2 or str(args[1]).lower() != "register-current"):
        return ctx.fail("mail artifact register-current [--scope metadata_only|excerpt|full_body]")
    return None


def _requested_scope(tokens: Sequence[str]) -> str:
    scope = (option(tokens, "scope") or "metadata_only").lower()
    return "excerpt" if scope == "body_excerpt" else scope


def _load_scoped_excerpt(ctx: MailCommandContext, row: Mapping[str, Any], tokens: Sequence[str], scope: str):
    """Authorize and load the body for ``excerpt``/``full_body`` scopes; returns (excerpt, access)."""
    if scope not in {"excerpt", "full_body"}:
        return "", None
    confirmation = "confirm-full-body" if scope == "full_body" else "confirm-body"
    release_scope = "full_body" if scope == "full_body" else "body_excerpt"
    access = authorize_content(
        ctx.application,
        row,
        tokens,
        release_scope=release_scope,
        confirmation_flag=confirmation,
        explicit_command=True,
    )
    excerpt = _body_text(as_mapping(ctx.application.load_body(_mail_message_key(row), access=access)))
    if scope == "excerpt":
        excerpt = excerpt[:1000]
    return excerpt, access


def _grant_artifact_to_goal(
    ctx: MailCommandContext, goal_id: str, scope: str, artifact: dict[str, Any]
) -> CommandResult:
    artifact_ref = str(artifact.get("artifact_ref") or "")
    grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{scope}'.encode('utf-8')).hexdigest()[:10]}"
    grant_payload = {
        "schema": "source_artifact_grant.v1",
        "grant_id": grant_id,
        "goal_id": goal_id,
        "artifact_ref": artifact_ref,
        "granted_by": "operator_tui_mail",
        "granted_at": now_iso(),
        "allowed_usages": ["read", "use_as_context"],
        "data_boundary": "project_private",
        "sensitivity": "internal",
        "policy_decision_ref": f"policy:mail:{scope}",
    }
    try:
        created = GoalArtifactService().create_grant(goal_id=goal_id, grant=grant_payload)
    except GoalArtifactServiceError as exc:
        return ctx.fail(f"mail grant failed: {exc.reason_code}")
    return ctx.view(f"mail granted to goal {goal_id}", output={"grant": created, "artifact": artifact})


def handle_mail_artifact_or_grant(ctx: MailCommandContext) -> CommandResult:
    """``artifact register-current`` and ``grant-current-to-goal <goal-id>``."""
    is_grant = str(ctx.args[0]).lower() == "grant-current-to-goal"
    usage_error = _artifact_usage_error(ctx, is_grant)
    if usage_error is not None:
        return usage_error
    goal_id = str(ctx.args[1]).strip() if is_grant else ""
    tokens = list(ctx.args[2:])
    scope = _requested_scope(tokens)
    row = selected_row(ctx.payload())
    if not row:
        return ctx.fail(f"mail {'grant' if is_grant else 'artifact'} failed: no selected message")
    try:
        excerpt, artifact_access = _load_scoped_excerpt(ctx, row, tokens, scope)
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail artifact content failed: {exc}")
    try:
        artifact = as_mapping(
            call_extension(
                ctx.application,
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
        return ctx.fail(f"mail artifact failed: {exc}")
    _record_current_artifact(ctx, artifact)
    if not is_grant:
        return ctx.view(f"mail artifact registered {artifact.get('artifact_ref')}", output={"artifact": artifact})
    return _grant_artifact_to_goal(ctx, goal_id, scope, artifact)


def handle_mail_revoke_grant(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 3:
        return ctx.fail("mail revoke-grant <goal-id> <grant-id>")
    goal_id = str(ctx.args[1]).strip()
    grant_id = str(ctx.args[2]).strip()
    try:
        revoked = GoalArtifactService().revoke_grant(goal_id=goal_id, grant_id=grant_id, revoke_reason="mail_revoke")
    except GoalArtifactServiceError as exc:
        return ctx.fail(f"mail revoke failed: {exc.reason_code}")
    return ctx.reply(f"mail grant revoked {grant_id}", revoked)


def handle_mail_context_envelope(ctx: MailCommandContext) -> CommandResult:
    if len(ctx.args) < 2:
        return ctx.fail("mail context-envelope <goal-id> [--target cloud_worker|local_worker]")
    goal_id = str(ctx.args[1]).strip()
    target = option(ctx.args[2:], "target") or "local_worker"
    try:
        envelope = as_mapping(
            call_extension(ctx.application, "build_context_envelope", goal_id=goal_id, worker_target=target)
        )
    except (MailApplicationError, ValueError) as exc:
        return ctx.fail(f"mail context-envelope failed: {exc}")
    return ctx.reply(f"mail context-envelope {goal_id} target={target}", _json_safe(envelope))
