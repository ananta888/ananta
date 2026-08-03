"""ALWA-009: Flask API for the persistent approval lifecycle.

- ``GET /api/approvals?status=pending`` lists requests (filters: status,
  task_id, goal_id). Responses carry digest prefixes and scope summaries,
  never raw arguments or content payloads.
- ``POST /api/approvals/<id>/decision`` accepts ``decision=granted|denied``
  with optional ``reason`` and bounded ``expires_at`` override.

Auth follows the existing route pattern (``@check_auth``). Errors:
400 invalid decision/expires_at, 404 unknown request, 409 already
decided/expired.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.services.approval_request_service import (
    ApprovalDecisionError,
    digest_prefix,
    get_approval_request_service,
)
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectCapability,
)

approvals_bp = Blueprint("approvals", __name__)

_SCOPE_SUMMARY_KEYS = {
    "approval_class",
    "pre_approval",
    "goal_id",
    "source",
    "reason_code",
    "plan_id",
    "source_task_id",
    "recovery_key",
    "decision_outcome",
    "organization_id",
    "project_id",
    "operation",
    "artifact_revision_id",
}

_APPROVER_ROLES = frozenset(
    {
        "approval_admin",
        "approval_approver",
        "approver",
        "operator",
    }
)


def _organization_principal():
    from agent.services.organization_membership_service import (
        OrganizationAccessPrincipal,
    )

    user = getattr(g, "user", {}) or {}
    authenticated = get_authenticated_source_control_principal()
    principal_id = str(authenticated.subject_id or "")
    tenant_id = str(authenticated.tenant_id or "")
    if user and not tenant_id and principal_id:
        try:
            from agent.services.user_session_tokens import local_user_tenant_id

            tenant_id = local_user_tenant_id(principal_id)
        except Exception:
            tenant_id = ""
    return OrganizationAccessPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        credential_type=str(user.get("credential_type") or "user"),
        project_id=str(authenticated.project_id or "").strip() or None,
    )


def _approval_scope(row: Any) -> tuple[str, str, str] | None:
    """Return one consistent persisted/legacy scope or fail closed on drift."""

    scope = dict(getattr(row, "scope", {}) or {})
    values: list[str] = []
    for field in ("tenant_id", "project_id", "organization_id"):
        persisted = str(getattr(row, field, None) or "").strip()
        legacy = str(scope.get(field) or "").strip()
        if persisted and legacy and persisted != legacy:
            return None
        values.append(persisted or legacy)
    return values[0], values[1], values[2]


def _has_project_access(
    *,
    tenant_id: str,
    project_id: str,
    capability: ProjectCapability,
) -> bool:
    authenticated = get_authenticated_source_control_principal()
    normalized_tenant = str(tenant_id or "").strip()
    normalized_project = str(project_id or "").strip()
    if (
        not normalized_tenant
        or not normalized_project
        or not authenticated.subject_id
        or (
            authenticated.tenant_id
            and str(authenticated.tenant_id) != normalized_tenant
        )
        or (
            authenticated.project_id
            and str(authenticated.project_id) != normalized_project
        )
    ):
        return False
    authority = current_app.extensions.get("project_access_authority")
    if authority is None:
        return False
    try:
        authority.require(
            tenant_id=normalized_tenant,
            project_id=normalized_project,
            subject_id=authenticated.subject_id,
            capability=capability,
            tenant_admin=authenticated.is_admin,
        )
    except ProjectAccessError:
        return False
    return True


def _scope_matches_principal(row: Any) -> bool:
    scope = _approval_scope(row)
    if scope is None:
        return False
    tenant_id, project_id, _organization_id = scope
    principal = _organization_principal()
    global_admin = bool(getattr(g, "is_admin", False)) and not principal.tenant_id

    # Truly tenant-less rows predate tenant isolation. They are deliberately
    # available only to the global Hub administrator; a tenant-bound identity
    # must never inherit them as a compatibility fallback.
    if not tenant_id:
        return global_admin and not project_id
    if principal.tenant_id != tenant_id and not global_admin:
        return False
    if project_id:
        return _has_project_access(
            tenant_id=tenant_id,
            project_id=project_id,
            capability=ProjectCapability.READ,
        )
    # A project-bound credential cannot access an unbound legacy resource.
    return principal.project_id is None


def _can_view_approval(row: Any) -> bool:
    scope = _approval_scope(row)
    if scope is None or not _scope_matches_principal(row):
        return False
    tenant_id, project_id, organization_id = scope
    if organization_id:
        from agent.services.organization_membership_service import (
            OrganizationMembershipService,
        )

        return OrganizationMembershipService().can_view(
            principal=_organization_principal(),
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
    if bool(getattr(g, "is_admin", False)):
        return True
    goal_id = str(getattr(row, "goal_id", "") or "").strip()
    try:
        from agent.services.goal_service import get_goal_service
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repositories = get_repository_registry()
        if goal_id:
            goal = repositories.goal_repo.get_by_id(goal_id)
            return get_goal_service().can_access_goal(
                goal,
                getattr(g, "user", {}) or {},
                False,
            )
        task_id = str(getattr(row, "task_id", "") or "").strip()
        if not task_id:
            return False
        task = repositories.task_repo.get_by_id(task_id)
        if task is None:
            return False
        task_goal_id = str(getattr(task, "goal_id", "") or "").strip()
        if task_goal_id:
            goal = repositories.goal_repo.get_by_id(task_goal_id)
            return get_goal_service().can_access_goal(
                goal,
                getattr(g, "user", {}) or {},
                False,
            )
        user = getattr(g, "user", {}) or {}
        principal_team_id = str(user.get("team_id") or "").strip()
        return bool(
            principal_team_id
            and principal_team_id == str(getattr(task, "team_id", "") or "")
        )
    except Exception:
        return False


def _can_decide_approval(row: Any) -> bool:
    """Authorize a Hub decision independently from approval visibility."""

    if not _can_view_approval(row):
        return False
    scope = _approval_scope(row)
    if scope is None:
        return False
    tenant_id, project_id, organization_id = scope
    authenticated = get_authenticated_source_control_principal()

    if organization_id:
        if not _has_project_access(
            tenant_id=tenant_id,
            project_id=project_id,
            capability=ProjectCapability.MANAGE,
        ):
            return False
        from agent.services.organization_membership_service import (
            OrganizationMembershipService,
        )

        operation = str(
            dict(getattr(row, "scope", {}) or {}).get("operation")
            or getattr(row, "tool_name", "")
        ).strip()
        return OrganizationMembershipService().can_mutate(
            principal=_organization_principal(),
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            grant_kind=f"approval:{operation}",
        )

    from agent.services.task_recovery_planning_service import (
        RECOVERY_MATERIALIZE_TOOL,
    )

    if str(getattr(row, "tool_name", "") or "") == RECOVERY_MATERIALIZE_TOOL:
        return bool(getattr(g, "is_admin", False))
    if bool(getattr(g, "is_admin", False)):
        return True
    if project_id:
        if _has_project_access(
            tenant_id=tenant_id,
            project_id=project_id,
            capability=ProjectCapability.MANAGE,
        ):
            return True
        if not (_APPROVER_ROLES & authenticated.roles):
            return False
        return _has_project_access(
            tenant_id=tenant_id,
            project_id=project_id,
            capability=ProjectCapability.READ,
        )
    return bool(tenant_id and (_APPROVER_ROLES & authenticated.roles))


def _request_to_payload(row) -> dict[str, Any]:
    return {
        "request_id": row.id,
        "task_id": row.task_id,
        "goal_id": row.goal_id,
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
        "organization_id": row.organization_id,
        "approval_intent_key": row.approval_intent_key,
        "trace_id": row.trace_id,
        "tool_name": row.tool_name,
        "digest_prefix": digest_prefix(row.arguments_digest),
        "target_fingerprint_prefix": digest_prefix(row.target_fingerprint),
        "risk_class": row.risk_class,
        "k_class": row.k_class,
        "governance_mode": row.governance_mode,
        "status": row.status,
        "scope_summary": {k: v for k, v in dict(row.scope or {}).items() if k in _SCOPE_SUMMARY_KEYS},
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "decided_at": row.decided_at,
        "decided_by": row.decided_by,
        "decision_reason": row.decision_reason,
        "consumed_at": row.consumed_at,
        "has_content_payload": bool(row.content_artifact_ref),
    }


@approvals_bp.get("/api/approvals")
@check_auth
def list_approvals():
    svc = get_approval_request_service()
    svc.expire_old_requests()
    status = str(request.args.get("status") or "").strip() or None
    task_id = str(request.args.get("task_id") or "").strip() or None
    goal_id = str(request.args.get("goal_id") or "").strip() or None
    organization_id = str(request.args.get("organization_id") or "").strip() or None
    requested_project_id = str(request.args.get("project_id") or "").strip() or None
    principal = _organization_principal()
    project_id = requested_project_id or principal.project_id
    if organization_id and not project_id:
        return jsonify({"error": "approval_project_scope_required"}), 400
    try:
        page_size = int(str(request.args.get("page_size") or "50"))
    except ValueError:
        return jsonify({"error": "approval_page_size_invalid"}), 400
    if page_size < 1 or page_size > 100:
        return jsonify({"error": "approval_page_size_invalid"}), 400
    from agent.services.organization_membership_service import (
        OrganizationMembershipService,
    )

    if project_id and not _has_project_access(
        tenant_id=principal.tenant_id,
        project_id=project_id,
        capability=ProjectCapability.READ,
    ):
        # Lists use an empty result while item endpoints use 404, keeping an
        # unknown project and an unauthorized project indistinguishable.
        return jsonify({"requests": [], "next_cursor": None})
    memberships = OrganizationMembershipService()
    allowed_organizations = memberships.authorized_organization_ids(
        principal=principal,
        project_id=project_id,
    )
    if organization_id and organization_id not in allowed_organizations:
        # Keep an unknown and an unauthorized Organization indistinguishable.
        return jsonify({"requests": [], "next_cursor": None})
    cursor_scope = {
        "principal_id": principal.principal_id,
        "tenant_id": principal.tenant_id,
        "project_id": project_id,
        "organization_id": organization_id,
        "goal_id": goal_id,
        "task_id": task_id,
        "status": status,
    }
    try:
        before_created_at, before_id = _decode_approval_cursor(
            str(request.args.get("cursor") or "").strip() or None,
            cursor_scope,
        )
    except ValueError:
        return jsonify({"error": "approval_cursor_invalid"}), 400
    rows = svc.list_requests(
        status=status,
        task_id=task_id,
        goal_id=goal_id,
        tenant_id=principal.tenant_id or None,
        project_id=project_id,
        organization_id=organization_id,
        organization_ids=allowed_organizations,
        scope_is_admin=bool(getattr(g, "is_admin", False)),
        scope_team_id=str((getattr(g, "user", {}) or {}).get("team_id") or "") or None,
        before_created_at=before_created_at,
        before_id=before_id,
        limit=page_size + 1,
    )
    visible_rows = [row for row in rows if _can_view_approval(row)]
    page = visible_rows[:page_size]
    has_more = len(rows) > page_size and len(page) == page_size
    next_cursor = (
        _encode_approval_cursor(page[-1], cursor_scope)
        if has_more and page
        else None
    )
    return jsonify(
        {
            "requests": [_request_to_payload(row) for row in page],
            "next_cursor": next_cursor,
        }
    )


def _approval_cursor_secret() -> bytes:
    secret = str(current_app.config.get("SECRET_KEY") or "").encode("utf-8")
    if not secret:
        raise ValueError("approval_cursor_secret_missing")
    return secret


def _approval_cursor_scope_digest(scope: dict[str, Any]) -> str:
    payload = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode_approval_cursor(row: Any, scope: dict[str, Any]) -> str:
    created_at = float(row.created_at)
    if not math.isfinite(created_at) or created_at < 0:
        raise ValueError("approval_cursor_invalid")
    payload = json.dumps(
        {
            "created_at": created_at,
            "id": str(row.id),
            "scope": _approval_cursor_scope_digest(scope),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_approval_cursor_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")


def _decode_approval_cursor(
    cursor: str | None,
    scope: dict[str, Any],
) -> tuple[float | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        if len(decoded) <= hashlib.sha256().digest_size:
            raise ValueError("approval_cursor_invalid")
        payload, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(_approval_cursor_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("approval_cursor_invalid")
        value = json.loads(payload)
        if (
            not isinstance(value, dict)
            or set(value) != {"created_at", "id", "scope"}
            or value.get("scope") != _approval_cursor_scope_digest(scope)
        ):
            raise ValueError("approval_cursor_invalid")
        created_at = float(value["created_at"])
        row_id = str(value["id"] or "").strip()
        if not math.isfinite(created_at) or created_at < 0 or not row_id:
            raise ValueError("approval_cursor_invalid")
        return created_at, row_id
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("approval_cursor_invalid") from exc


@approvals_bp.get("/api/approvals/<request_id>")
@check_auth
def get_approval(request_id: str):
    row = get_approval_request_service().get_request(request_id)
    if row is None or not _can_view_approval(row):
        return jsonify({"error": "request_not_found"}), 404
    return jsonify(_request_to_payload(row))


@approvals_bp.post("/api/approvals/<request_id>/decision")
@check_auth
def decide_approval(request_id: str):
    body = request.get_json(silent=True) or {}
    decision = str(body.get("decision") or "").strip().lower()
    reason = str(body.get("reason") or "").strip() or None
    expires_at = body.get("expires_at")
    user = getattr(g, "user", {}) or {}
    decided_by = str(
        user.get("sub") or user.get("username") or "operator"
    )
    service = get_approval_request_service()
    pending_request = service.get_request(request_id)
    if pending_request is None or not _can_view_approval(pending_request):
        return jsonify({"error": "request_not_found"}), 404
    if not _can_decide_approval(pending_request):
        return jsonify({"error": "forbidden"}), 403
    try:
        row = service.decide_request(
            request_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            expires_at=expires_at,
        )
    except ApprovalDecisionError as exc:
        return jsonify({"error": exc.code}), exc.http_status
    return jsonify(_request_to_payload(row))
