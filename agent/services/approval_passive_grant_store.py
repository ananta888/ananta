"""Passive domain grant markers inside a caller-owned Unit of Work.

Planning transitions create or consume digest-bound approval markers in the
same session as their domain write. This store never opens, commits or rolls
back a session; the unique approval-intent index is its concurrency boundary.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import ApprovalRequestDB
from agent.services.approval_request_digest import (
    _normalize_value,
    canonicalize_tool_call,
    compute_arguments_digest,
)
from agent.services.approval_request_errors import ApprovalDecisionError

PASSIVE_PLANNING_APPROVAL_TOOLS = frozenset(
    {
        "planning.category.promote",
        "planning.track.adopt",
        "planning.track.materialize",
        "planning.proposal.amend",
    }
)


class ApprovalPassiveGrantStore:
    """Get/create and consume passive approval markers in a caller session."""

    def consume_bound_request_in_session(
        self,
        session: Session,
        *,
        request_id: str,
        tool_name: str,
        approval_intent_key: str,
        tenant_id: str,
        project_id: str,
        goal_id: str,
        organization_id: str,
    ) -> ApprovalRequestDB:
        """Consume one exact passive grant inside the caller's Unit of Work."""
        request = session.get(ApprovalRequestDB, str(request_id or ""))
        if request is None:
            raise ApprovalDecisionError("request_not_found", 404)
        if str(request.tool_name or "") != str(tool_name or ""):
            raise ApprovalDecisionError("approval_tool_mismatch", 409)
        if str(request.approval_intent_key or "") != str(approval_intent_key or ""):
            raise ApprovalDecisionError("approval_intent_mismatch", 409)
        if str(request.tenant_id or "") != str(tenant_id or ""):
            raise ApprovalDecisionError("approval_tenant_mismatch", 409)
        if str(request.project_id or "") != str(project_id or ""):
            raise ApprovalDecisionError("approval_project_mismatch", 409)
        if str(request.goal_id or "") != str(goal_id or ""):
            raise ApprovalDecisionError("approval_goal_mismatch", 409)
        if str(request.organization_id or "") != str(organization_id or ""):
            raise ApprovalDecisionError("approval_organization_mismatch", 409)
        if request.expires_at is not None and float(request.expires_at) < time.time():
            raise ApprovalDecisionError("request_expired", 409)
        transition = session.exec(
            sa_update(ApprovalRequestDB)
            .where(
                ApprovalRequestDB.id == str(request_id or ""),
                ApprovalRequestDB.status == "granted",
                ApprovalRequestDB.approval_intent_key == str(approval_intent_key or ""),
            )
            .values(status="consumed", consumed_at=time.time())
        )
        if int(getattr(transition, "rowcount", 0) or 0) != 1:
            raise ApprovalDecisionError(f"request_not_granted:{request.status}", 409)
        session.flush()
        refreshed = session.get(ApprovalRequestDB, str(request_id or ""))
        if refreshed is None:
            raise ApprovalDecisionError("request_not_found", 404)
        return refreshed

    def ensure_passive_request_in_session(
        self,
        session: Session,
        *,
        tool_name: str,
        approval_intent_key: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        arguments: dict[str, Any],
        target_fingerprint: str,
        scope: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> ApprovalRequestDB:
        """Atomically get/create a passive domain grant marker in a caller UoW."""
        if str(tool_name or "") not in PASSIVE_PLANNING_APPROVAL_TOOLS:
            raise ValueError("passive_approval_tool_forbidden")
        normalized_intent = str(approval_intent_key or "").strip().lower()
        if len(normalized_intent) != 64 or any(char not in "0123456789abcdef" for char in normalized_intent):
            raise ValueError("approval_intent_key_invalid")
        bindings = {
            "tool_name": str(tool_name or "").strip(),
            "tenant_id": str(tenant_id or "").strip(),
            "project_id": str(project_id or "").strip(),
            "organization_id": str(organization_id or "").strip(),
            "goal_id": str(goal_id or "").strip(),
            "target_fingerprint": str(target_fingerprint or "").strip(),
        }
        for field, value in bindings.items():
            if not value:
                raise ValueError(f"passive_approval_{field}_required")
        canonical, content_payload, content_hash = canonicalize_tool_call(
            bindings["tool_name"],
            arguments,
        )
        if content_payload is not None or content_hash is not None:
            raise ValueError("passive_approval_content_forbidden")
        arguments_digest = compute_arguments_digest(
            bindings["tool_name"],
            canonical,
            bindings["target_fingerprint"],
        )
        existing = session.exec(
            select(ApprovalRequestDB).where(ApprovalRequestDB.approval_intent_key == normalized_intent)
        ).one_or_none()
        if existing is not None:
            self.validate_passive_request_binding(
                existing,
                canonical_arguments=canonical,
                arguments_digest=arguments_digest,
                **bindings,
            )
            return existing
        request = ApprovalRequestDB(
            id=str(uuid.uuid4()),
            task_id=None,
            goal_id=bindings["goal_id"],
            tenant_id=bindings["tenant_id"],
            project_id=bindings["project_id"],
            organization_id=bindings["organization_id"],
            approval_intent_key=normalized_intent,
            tool_name=bindings["tool_name"],
            canonical_arguments=canonical,
            arguments_digest=arguments_digest,
            target_fingerprint=bindings["target_fingerprint"],
            risk_class="high",
            governance_mode="strict",
            status="pending",
            scope={
                key: value
                for key, value in dict(scope or {}).items()
                if key
                not in {
                    "prompt",
                    "raw_messages",
                    "raw_response",
                    "content",
                    "unified_diff",
                    "file_content",
                }
            },
            created_at=time.time(),
            expires_at=time.time() + max(60, min(int(ttl_seconds), 7 * 24 * 3600)),
        )
        request_added_in_savepoint = False
        try:
            # The unique approval-intent index is the authoritative concurrency
            # boundary.  Keep the INSERT in a savepoint so a losing writer can
            # recover without rolling back unrelated state in the caller's UoW.
            with session.begin_nested():
                session.add(request)
                request_added_in_savepoint = True
                session.flush([request])
            return request
        except IntegrityError as exc:
            if not request_added_in_savepoint:
                raise
            authoritative = session.exec(
                select(ApprovalRequestDB).where(ApprovalRequestDB.approval_intent_key == normalized_intent)
            ).one_or_none()
            if authoritative is None:
                # An unrelated constraint failed, or the competing transaction
                # is not visible at this isolation level.  Either way, fail
                # closed while leaving the outer transaction usable.
                raise ApprovalDecisionError(
                    "approval_request_persistence_conflict",
                    409,
                ) from exc
            self.validate_passive_request_binding(
                authoritative,
                canonical_arguments=canonical,
                arguments_digest=arguments_digest,
                **bindings,
            )
            return authoritative

    @staticmethod
    def validate_passive_request_binding(
        request: ApprovalRequestDB,
        *,
        tool_name: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        canonical_arguments: dict[str, Any],
        arguments_digest: str,
        target_fingerprint: str,
    ) -> None:
        """Fail closed unless an intent-key replay is the exact same request."""
        if (
            str(request.tool_name or "") != tool_name
            or str(request.tenant_id or "") != tenant_id
            or str(request.project_id or "") != project_id
            or str(request.organization_id or "") != organization_id
            or str(request.goal_id or "") != goal_id
            or _normalize_value(dict(request.canonical_arguments or {})) != _normalize_value(canonical_arguments)
            or str(request.arguments_digest or "") != arguments_digest
            or str(request.target_fingerprint or "") != target_fingerprint
        ):
            raise ApprovalDecisionError("approval_intent_conflict", 409)
