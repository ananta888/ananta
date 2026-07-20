"""Read-only HTTP projection for content-free semantic-media diagnostics."""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_user_auth
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditRecorder,
)
from agent.services.semantic_media_debug_read_model import (
    SemanticMediaDebugPrincipal,
    SemanticMediaDebugReadModel,
)

semantic_media_debug_bp = Blueprint("semantic_media_debug", __name__)


@semantic_media_debug_bp.get("/v1/semantic-media/debug/events")
@check_user_auth
def semantic_media_debug_events():
    try:
        identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
        tenant = str(identity.get("tenant_id") or identity.get("tenant") or "").strip()
        subject = str(identity.get("sub") or identity.get("username") or "").strip()
        if not tenant or not subject:
            raise SemanticMediaAuditError("semantic_debug_unauthenticated", status_code=401)
        roles = _roles(identity)
        read_model = current_app.extensions.get("semantic_media_debug_read_model")
        if not isinstance(read_model, SemanticMediaDebugReadModel):
            raise SemanticMediaAuditError("semantic_debug_unavailable", status_code=503)
        recorder = current_app.extensions.get("semantic_media_audit_recorder")
        if not isinstance(recorder, SemanticMediaAuditRecorder):
            raise SemanticMediaAuditError("semantic_debug_unavailable", status_code=503)
        scope_digest = _scope_digest(recorder)
        result = read_model.page(
            SemanticMediaDebugPrincipal(
                recorder.digest("tenant", tenant),
                recorder.digest("subject", subject),
                frozenset(roles),
            ),
            scope_digest=scope_digest,
            cursor=str(request.args["cursor"]) if "cursor" in request.args else None,
            limit=_limit(request.args.get("limit", "50")),
        )
        return jsonify({"ok": True, "data": result}), 200
    except SemanticMediaAuditError as exc:
        return jsonify({"ok": False, "error": {"code": exc.reason_code, "retriable": False}}), exc.status_code


def _roles(identity: dict) -> tuple[str, ...]:
    values: list[str] = []
    singular = identity.get("role")
    if isinstance(singular, str) and singular:
        values.append(singular)
    direct = identity.get("roles")
    if isinstance(direct, list):
        values.extend(str(value) for value in direct)
    realm = identity.get("realm_access")
    if isinstance(realm, dict) and isinstance(realm.get("roles"), list):
        values.extend(str(value) for value in realm["roles"])
    return tuple(values)


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise SemanticMediaAuditError("semantic_debug_limit_invalid", status_code=400) from exc
    if not 1 <= limit <= 100:
        raise SemanticMediaAuditError("semantic_debug_limit_invalid", status_code=400)
    return limit


def _scope_digest(recorder: SemanticMediaAuditRecorder) -> str:
    """Resolve a logical read scope without ever returning its clear value.

    Existing operators may continue to pass a pre-computed digest.  Browser
    clients use the additive logical-scope form because the HMAC key belongs
    exclusively to the Hub.  Supplying both is rejected to avoid ambiguous
    authorization and pagination bindings.
    """

    supplied_digest = str(request.args.get("scope_digest") or "")
    supplied_scope = str(request.args.get("scope") or "")
    if bool(supplied_digest) == bool(supplied_scope):
        raise SemanticMediaAuditError("semantic_debug_scope_invalid", status_code=400)
    if supplied_digest:
        if len(supplied_digest) != 64 or any(value not in "0123456789abcdef" for value in supplied_digest):
            raise SemanticMediaAuditError("semantic_debug_scope_invalid", status_code=400)
        return supplied_digest
    if (
        not 8 <= len(supplied_scope) <= 256
        or any(character.isspace() for character in supplied_scope)
        or not supplied_scope.startswith(("semantic-contract:", "semantic-media-session:", "speech-job:"))
    ):
        raise SemanticMediaAuditError("semantic_debug_scope_invalid", status_code=400)
    return recorder.digest("scope", supplied_scope)


__all__ = ["semantic_media_debug_bp"]
