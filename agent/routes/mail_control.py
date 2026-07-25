"""Authenticated Hub control-plane API for provider-neutral mail operations."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Mapping

from flask import Blueprint, jsonify, request

from agent.auth import check_auth
from agent.services.mail_application_service import (
    MailApplicationError,
    get_mail_application_service,
)
from agent.services.mail_runtime_policy import (
    get_mail_health_registry,
    get_mail_runtime_policy,
)
from agent.services.mail_operation_intent_service import (
    get_mail_operation_intent_service,
)
from agent.services.mail_provider_ports import MailContentAccessRequest
from agent.services.mail_task_service import (
    MAIL_OPERATIONS,
    MailWorkspaceScope,
    get_mail_task_service,
)

mail_control_bp = Blueprint("mail_control", __name__, url_prefix="/api/mail")

_PREVIEWS: dict[str, dict[str, Any]] = {}
_PREVIEW_LOCK = threading.RLock()
_PREVIEW_TTL_SECONDS = 900


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True) or {}
    if not isinstance(value, Mapping):
        raise MailApplicationError("mail_request_object_required")
    return dict(value)


def _workspace_scope(value: Mapping[str, Any]) -> MailWorkspaceScope:
    workspace_id = str(value.get("workspace_id") or "repo").strip() or "repo"
    from agent.services.ops_registry_service import get_ops_registry_service

    if get_ops_registry_service().resolve_workspace(workspace_id) is None:
        raise PermissionError("workspace_not_allowed")
    return MailWorkspaceScope(
        workspace_id=workspace_id,
        tenant_id=str(value.get("tenant_id") or "").strip(),
    )


def _actor() -> str:
    return "mail-control-api"


def _preview_ref(draft: Mapping[str, Any]) -> str:
    material = json.dumps(
        dict(draft),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"mail-preview:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _store_preview(draft: Mapping[str, Any]) -> str:
    ref = _preview_ref(draft)
    now = time.time()
    with _PREVIEW_LOCK:
        expired = [
            key
            for key, value in _PREVIEWS.items()
            if float(value.get("expires_at") or 0.0) <= now
        ]
        for key in expired:
            _PREVIEWS.pop(key, None)
        if len(_PREVIEWS) >= 100:
            oldest = min(
                _PREVIEWS,
                key=lambda key: float(_PREVIEWS[key].get("created_at") or 0.0),
            )
            _PREVIEWS.pop(oldest, None)
        _PREVIEWS[ref] = {
            "draft": dict(draft),
            "created_at": now,
            "expires_at": now + _PREVIEW_TTL_SECONDS,
        }
    return ref


def _load_preview(ref: str) -> dict[str, Any]:
    now = time.time()
    with _PREVIEW_LOCK:
        entry = _PREVIEWS.get(str(ref))
        if entry is None or float(entry.get("expires_at") or 0.0) <= now:
            _PREVIEWS.pop(str(ref), None)
            raise MailApplicationError("mail_account_preview_expired")
        return dict(entry["draft"])


def _error(exc: Exception):
    reason = str(exc) or "mail_request_failed"
    if not (
        reason.startswith("mail_")
        or reason in {"workspace_not_allowed", "task_not_found"}
    ):
        reason = "mail_request_failed"
    status = 403 if isinstance(exc, PermissionError) else 400
    if reason in {"mail_task_not_found", "mail_message_not_found"}:
        status = 404
    return jsonify({"ok": False, "reason_code": reason}), status


@mail_control_bp.get("/accounts")
@check_auth
def list_mail_accounts():
    try:
        return jsonify(
            {
                "ok": True,
                "accounts": get_mail_application_service().list_accounts(),
            }
        )
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/accounts/preview")
@check_auth
def preview_mail_account():
    try:
        body = _body()
        preview = get_mail_application_service().preview_account(
            account_id=str(body.get("account_id") or ""),
            display_name=str(body.get("display_name") or ""),
            requested_protocol=str(body.get("requested_protocol") or "auto"),
            username_ref=str(body.get("username_ref") or ""),
            credential_ref=str(body.get("credential_ref") or ""),
            sync_policy=str(body.get("sync_policy") or "manual"),
            provider_config=dict(body.get("provider_config") or {}),
        )
        preview_ref = _store_preview(dict(preview.pop("draft")))
        return jsonify({"ok": True, "preview_ref": preview_ref, **preview})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/accounts/discover")
@check_auth
def discover_mail_account():
    try:
        body = _body()
        preview_ref = str(body.get("preview_ref") or "")
        task = get_mail_application_service().request_discovery(
            preview=_load_preview(preview_ref),
            workspace=_workspace_scope(body),
            idempotency_key=str(body.get("idempotency_key") or preview_ref),
            actor_ref=_actor(),
        )
        return jsonify({"ok": True, "task": task}), 202
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/accounts/confirm")
@check_auth
def confirm_mail_account():
    try:
        body = _body()
        preview_ref = str(body.get("preview_ref") or "")
        result = get_mail_application_service().confirm_account(
            preview=_load_preview(preview_ref),
            resolved_protocol=str(body.get("resolved_protocol") or "") or None,
            discovery_task_id=str(body.get("discovery_task_id") or "") or None,
        )
        with _PREVIEW_LOCK:
            _PREVIEWS.pop(preview_ref, None)
        return jsonify({"ok": True, "account": result}), 201
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/accounts/<account_id>/disable")
@check_auth
def disable_mail_account(account_id: str):
    try:
        result = get_mail_application_service().disable_account(account_id)
        get_mail_task_service().cancel_account(
            account_ref=f"mail-account:{account_id}",
            actor=_actor(),
        )
        return jsonify({"ok": True, "account": result})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/tasks")
@check_auth
def submit_mail_task():
    try:
        body = _body()
        operation = str(body.get("operation") or "").strip().lower()
        if operation not in MAIL_OPERATIONS:
            raise MailApplicationError("mail_task_operation_invalid")
        get_mail_runtime_policy().require_network_operation(operation)
        account_id = str(body.get("account_id") or "").strip()
        if not account_id:
            raise MailApplicationError("mail_task_account_required")
        workspace_scope = _workspace_scope(body)
        idempotency_key = str(body.get("idempotency_key") or "")
        policy_refs = body.get("policy_refs") or {}
        if not isinstance(policy_refs, Mapping):
            raise MailApplicationError("mail_task_policy_refs_invalid")
        operation_refs = body.get("operation_refs") or {}
        if not isinstance(operation_refs, Mapping):
            raise MailApplicationError("mail_task_operation_refs_invalid")
        intent = None
        if operation in {"body", "mutation"}:
            if body.get("explicit_confirmation") is not True:
                raise MailApplicationError(
                    "mail_operation_confirmation_required"
                )
            grant_ref = str(body.get("grant_ref") or "").strip()
            if not grant_ref:
                raise MailApplicationError("mail_operation_grant_ref_required")
            if operation == "body":
                message_ref = (
                    get_mail_application_service()
                    .get_provider_message_ref(
                        str(body.get("mail_ref_id") or "")
                    )
                    .to_dict()
                )
                if message_ref["account_id"] != account_id:
                    raise MailApplicationError(
                        "mail_operation_account_mismatch"
                    )
                intent_payload = {
                    "message_ref": message_ref,
                    "release_scope": str(
                        body.get("release_scope") or "full_body"
                    ),
                }
            else:
                raw_ids = body.get("mail_ref_ids") or []
                if not isinstance(raw_ids, list) or not raw_ids:
                    raise MailApplicationError(
                        "mail_mutation_message_refs_required"
                    )
                message_refs = [
                    get_mail_application_service()
                    .get_provider_message_ref(str(mail_ref_id))
                    .to_dict()
                    for mail_ref_id in raw_ids
                ]
                if any(
                    ref["account_id"] != account_id
                    for ref in message_refs
                ):
                    raise MailApplicationError(
                        "mail_operation_account_mismatch"
                    )
                intent_payload = {
                    "action": str(body.get("action") or ""),
                    "message_refs": message_refs,
                    "add_keywords": list(body.get("add_keywords") or []),
                    "remove_keywords": list(
                        body.get("remove_keywords") or []
                    ),
                    "destination_mailbox_ref_ids": list(
                        body.get("destination_mailbox_ref_ids") or []
                    ),
                    "if_in_state": str(body.get("if_in_state") or ""),
                    "permanent": bool(body.get("permanent", False)),
                    "intent_ref": str(body.get("mutation_intent_ref") or ""),
                    "audit_ref": str(body.get("audit_ref") or ""),
                    "confirmation_ref": str(
                        body.get("confirmation_ref") or ""
                    ),
                }
            intent = get_mail_operation_intent_service().create(
                operation=operation,
                account_id=account_id,
                workspace_id=workspace_scope.workspace_id,
                grant_ref=grant_ref,
                idempotency_key=idempotency_key,
                payload=intent_payload,
                ttl_seconds=int(body.get("intent_ttl_seconds") or 300),
            )
            operation_refs = {"intent_ref": intent.intent_ref}
        task = get_mail_task_service().submit(
            operation=operation,
            account_ref=f"mail-account:{account_id}",
            workspace_scope=workspace_scope,
            idempotency_key=idempotency_key,
            policy_refs=dict(policy_refs),
            operation_refs=dict(operation_refs),
            actor=_actor(),
            deadline_seconds=int(body.get("deadline_seconds") or 300),
            max_attempts=int(body.get("max_attempts") or 3),
        )
        if intent is not None:
            get_mail_operation_intent_service().bind_job(
                intent_ref=intent.intent_ref,
                job_id=str(task["job_id"]),
            )
        return jsonify({"ok": True, "task": task}), 202
    except Exception as exc:
        return _error(exc)


@mail_control_bp.get("/tasks/<job_id>")
@check_auth
def get_mail_task(job_id: str):
    try:
        task = get_mail_task_service().get_task(job_id)
        if task is None:
            raise MailApplicationError("mail_task_not_found")
        return jsonify({"ok": True, "task": task})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/tasks/<job_id>/cancel")
@check_auth
def cancel_mail_task(job_id: str):
    try:
        task = get_mail_task_service().cancel(job_id=job_id, actor=_actor())
        return jsonify({"ok": True, "task": task})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/tasks/<job_id>/retry")
@check_auth
def retry_mail_task(job_id: str):
    try:
        task = get_mail_task_service().retry(job_id=job_id, actor=_actor())
        return jsonify({"ok": True, "task": task}), 202
    except Exception as exc:
        return _error(exc)


@mail_control_bp.get("/messages")
@check_auth
def search_mail_metadata():
    try:
        query = str(request.args.get("q") or "")
        account_id = str(request.args.get("account_id") or "") or None
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
        rows = get_mail_application_service().search_message_metadata(
            query,
            account_id=account_id,
            limit=limit,
        )
        return jsonify({"ok": True, "messages": rows, "metadata_only": True})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/messages/<mail_ref_id>/body")
@check_auth
def read_mail_body(mail_ref_id: str):
    try:
        body = _body()
        metadata = get_mail_application_service().get_message_metadata(mail_ref_id)
        access = get_mail_application_service().authorize_operator_content(
            mail_ref_id=mail_ref_id,
            account_id=str(metadata.get("account_id") or ""),
            workspace_id=_workspace_scope(body).workspace_id,
            artifact_ref=(
                f"mail://{mail_ref_id}?scope="
                f"{str(body.get('release_scope') or 'full_body')}"
            ),
            grant_ref=str(body.get("grant_ref") or ""),
            release_scope=str(body.get("release_scope") or "full_body"),
            explicit_confirmation=body.get("explicit_confirmation") is True,
        )
        content = get_mail_application_service().load_body(
            mail_ref_id,
            access=access,
        )
        return jsonify({"ok": True, "mail_ref_id": mail_ref_id, "body": content})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post(
    "/messages/<mail_ref_id>/attachments/<attachment_id>"
)
@check_auth
def read_mail_attachment(mail_ref_id: str, attachment_id: str):
    try:
        body = _body()
        metadata = get_mail_application_service().get_message_metadata(mail_ref_id)
        access = get_mail_application_service().authorize_operator_content(
            mail_ref_id=mail_ref_id,
            account_id=str(metadata.get("account_id") or ""),
            workspace_id=_workspace_scope(body).workspace_id,
            artifact_ref=f"mail://{mail_ref_id}?scope=attachment_ref",
            grant_ref=str(body.get("grant_ref") or ""),
            release_scope="attachment_ref",
            explicit_confirmation=body.get("explicit_confirmation") is True,
        )
        attachment = get_mail_application_service().load_attachment(
            mail_ref_id,
            attachment_id,
            access=access,
        )
        return jsonify(
            {
                "ok": True,
                "mail_ref_id": mail_ref_id,
                "attachment": attachment,
            }
        )
    except Exception as exc:
        return _error(exc)


@mail_control_bp.get("/health")
@check_auth
def mail_health():
    return jsonify(
        {
            "ok": True,
            "runtime": get_mail_runtime_policy().snapshot().to_dict(),
            "health": get_mail_health_registry().snapshot(),
        }
    )


@mail_control_bp.post("/diagnose")
@check_auth
def diagnose_mail_account():
    try:
        body = _body()
        body["operation"] = "diagnose"
        operation = "diagnose"
        get_mail_runtime_policy().require_network_operation(operation)
        account_id = str(body.get("account_id") or "").strip()
        task = get_mail_task_service().submit(
            operation=operation,
            account_ref=f"mail-account:{account_id}",
            workspace_scope=_workspace_scope(body),
            idempotency_key=str(body.get("idempotency_key") or ""),
            policy_refs={
                "diagnostic_policy_ref": str(
                    body.get("diagnostic_policy_ref")
                    or "policy:mail:diagnostic:v1"
                )
            },
            actor=_actor(),
        )
        return jsonify({"ok": True, "task": task}), 202
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/internal/intents/resolve")
@check_auth
def resolve_mail_operation_intent():
    try:
        body = _body()
        account_ref = str(body.get("account_ref") or "")
        account_id = (
            account_ref.split(":", 1)[1]
            if account_ref.startswith("mail-account:")
            else account_ref
        )
        workspace = MailWorkspaceScope.from_mapping(
            dict(body.get("workspace_scope") or {})
        )
        result = get_mail_operation_intent_service().resolve(
            intent_ref=str(body.get("intent_ref") or ""),
            job_id=str(body.get("job_id") or ""),
            operation=str(body.get("operation") or ""),
            account_id=account_id,
            workspace_id=workspace.workspace_id,
        )
        if not result.ok or result.value is None:
            raise MailApplicationError(result.reason_code)
        return jsonify({"ok": True, "intent": result.value.to_dict()})
    except Exception as exc:
        return _error(exc)


@mail_control_bp.post("/internal/intents/authorize-content")
@check_auth
def authorize_mail_operation_content():
    try:
        body = _body()
        account_ref = str(body.get("account_ref") or "")
        account_id = (
            account_ref.split(":", 1)[1]
            if account_ref.startswith("mail-account:")
            else account_ref
        )
        workspace = MailWorkspaceScope.from_mapping(
            dict(body.get("workspace_scope") or {})
        )
        resolved = get_mail_operation_intent_service().resolve(
            intent_ref=str(body.get("intent_ref") or ""),
            job_id=str(body.get("job_id") or ""),
            operation="body",
            account_id=account_id,
            workspace_id=workspace.workspace_id,
        )
        if not resolved.ok or resolved.value is None:
            raise MailApplicationError(resolved.reason_code)
        access_request = body.get("access_request")
        if not isinstance(access_request, Mapping):
            raise MailApplicationError(
                "mail_content_access_request_invalid"
            )
        decision = get_mail_operation_intent_service().authorize_content(
            intent=resolved.value,
            request=MailContentAccessRequest(
                account_id=str(access_request.get("account_id") or ""),
                workspace_id=str(
                    access_request.get("workspace_id") or ""
                ),
                artifact_ref=str(
                    access_request.get("artifact_ref") or ""
                ),
                mail_ref_id=str(
                    access_request.get("mail_ref_id") or ""
                ),
                grant_ref=str(access_request.get("grant_ref") or ""),
                release_scope=str(
                    access_request.get("release_scope") or ""
                ),
            ),
        )
        if not decision.ok or decision.value is None:
            raise MailApplicationError(decision.reason_code)
        value = decision.value
        return jsonify(
            {
                "ok": True,
                "decision": {
                    "allowed": value.allowed,
                    "reason_code": value.reason_code,
                    "policy_decision_ref": value.policy_decision_ref,
                    "expires_at": value.expires_at,
                    "nonce": value.nonce,
                },
            }
        )
    except Exception as exc:
        return _error(exc)


__all__ = ["mail_control_bp"]
