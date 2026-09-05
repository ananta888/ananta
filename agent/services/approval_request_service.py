"""ALWA-002/003/008/011: persistent, digest-bound approval lifecycle.

Grants are bound to canonicalized arguments, never to tool names alone
(ALWA-DD-001). Content-bearing argument fields (file content, unified
diffs) are replaced by their SHA-256 before digest computation and
persisted as a hub payload artifact referenced via
``content_artifact_ref`` (ALWA-DD-007) — the raw payload never lands in
``scope`` or the audit log, and re-execution verifies the loaded payload
against ``content_hash``.

States: pending -> granted | denied | expired | superseded;
granted -> consumed | expired. Every transition is audited via
``log_audit`` with digest prefixes instead of raw arguments.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import and_, exists, or_
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.config import settings
from agent.db_models import ApprovalRequestDB, GoalDB, TaskDB
from agent.services.approval_auto_grant_policy import ApprovalAutoGrantPolicy

log = logging.getLogger(__name__)

CONTENT_BEARING_FIELDS = ("content", "unified_diff")
_DIGEST_PREFIX_LEN = 12
_PAYLOAD_REF_PREFIX = "approval-payload:"

AUDIT_APPROVAL_REQUEST_CREATED = "approval_request_created"
AUDIT_APPROVAL_REQUEST_DECIDED = "approval_request_decided"
AUDIT_APPROVAL_REQUEST_CONSUMED = "approval_request_consumed"
AUDIT_APPROVAL_REQUEST_EXPIRED = "approval_request_expired"
AUDIT_APPROVAL_REQUEST_SUPERSEDED = "approval_request_superseded"
AUDIT_APPROVAL_LEGACY_BYPASS_USED = "approval_legacy_bypass_used"
AUDIT_APPROVAL_REQUEST_REDISPATCH = "approval_request_redispatch"
AUDIT_APPROVAL_DOMAIN_ACTION_FAILED = "approval_domain_action_failed"

PASSIVE_PLANNING_APPROVAL_TOOLS = frozenset(
    {
        "planning.category.promote",
        "planning.track.adopt",
        "planning.track.materialize",
        "planning.proposal.amend",
    }
)


class ApprovalDecisionError(ValueError):
    """Raised for invalid lifecycle transitions (maps to HTTP 400/404/409)."""

    def __init__(self, code: str, http_status: int = 400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _engine():
    from agent.database import engine

    return engine


def _normalize_value(value: Any) -> Any:
    """Deterministic normalization: dicts sorted via json, None kept, no NaN."""
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, float) and value != value:  # NaN is not canonicalizable
        return None
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Return (canonical_arguments, content_payload, content_hash).

    ALWA-DD-007: content-bearing fields are extracted into a payload dict
    and replaced in the canonical arguments by
    ``{"__content_hash__": sha256}`` so the digest stays bound to the
    exact content without persisting it.
    """
    normalized = _normalize_value(dict(arguments or {}))
    payload: dict[str, Any] = {}
    for field in CONTENT_BEARING_FIELDS:
        if field in normalized and isinstance(normalized[field], str) and normalized[field]:
            payload[field] = normalized[field]
            normalized[field] = {"__content_hash__": _sha256_text(payload[field])}
    content_hash = None
    if payload:
        content_hash = _sha256_text(
            json.dumps(_normalize_value(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        )
    return normalized, (payload or None), content_hash


def compute_arguments_digest(
    tool_name: str,
    canonical_arguments: dict[str, Any],
    target_fingerprint: str | None = None,
) -> str:
    canonical_json = json.dumps(
        _normalize_value(canonical_arguments), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    raw = "\x00".join([str(tool_name or "").strip(), canonical_json, str(target_fingerprint or "")])
    return _sha256_text(raw)


def digest_prefix(digest: str | None) -> str:
    return str(digest or "")[:_DIGEST_PREFIX_LEN]


def canonical_approval_intent_key(
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
    goal_id: str,
    operation: str,
    artifact_revision_id: str,
    artifact_digest: str,
    policy_hash: str,
) -> str:
    fields = (
        tenant_id,
        project_id,
        organization_id,
        goal_id,
        operation,
        artifact_revision_id,
        artifact_digest,
        policy_hash,
    )
    normalized = tuple(str(value or "").strip() for value in fields)
    if any(not value for value in normalized):
        raise ValueError("approval_intent_binding_required")
    return _sha256_text("\x00".join(normalized))


class ApprovalRequestService:
    """Lifecycle of digest-bound ApprovalRequests (hub side)."""

    def __init__(
        self,
        *,
        auto_grant_policy: ApprovalAutoGrantPolicy | None = None,
    ) -> None:
        self._auto_grant_policy = auto_grant_policy or ApprovalAutoGrantPolicy()

    # --- payload store (ALWA-DD-007) -----------------------------------------

    @staticmethod
    def _payload_dir() -> Path:
        return Path(settings.data_dir) / "approval-payloads"

    def _store_content_payload(self, payload: dict[str, Any], content_hash: str) -> str:
        path = self._payload_dir() / f"{content_hash}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
            )
        return f"{_PAYLOAD_REF_PREFIX}{content_hash}"

    def load_content_payload(self, content_artifact_ref: str | None, content_hash: str | None) -> dict[str, Any] | None:
        """Load + verify a stored payload; returns None on missing/hash mismatch."""
        ref = str(content_artifact_ref or "")
        if not ref.startswith(_PAYLOAD_REF_PREFIX) or not content_hash:
            return None
        path = self._payload_dir() / f"{ref[len(_PAYLOAD_REF_PREFIX) :]}.json"
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        actual = _sha256_text(
            json.dumps(_normalize_value(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        )
        if actual != content_hash:
            log.warning("approval payload hash mismatch for %s", ref)
            return None
        return payload if isinstance(payload, dict) else None

    # --- audit ----------------------------------------------------------------

    @staticmethod
    def _audit(action: str, request: ApprovalRequestDB, extra: dict[str, Any] | None = None) -> None:
        try:
            from agent.common.audit import log_audit

            log_audit(
                action,
                {
                    "request_id": request.id,
                    "task_id": request.task_id,
                    "goal_id": request.goal_id,
                    "tenant_id": request.tenant_id,
                    "project_id": request.project_id,
                    "organization_id": request.organization_id,
                    "approval_intent_key_prefix": digest_prefix(request.approval_intent_key),
                    "trace_id": request.trace_id,
                    "tool_name": request.tool_name,
                    "digest_prefix": digest_prefix(request.arguments_digest),
                    "status": request.status,
                    "risk_class": request.risk_class,
                    "governance_mode": request.governance_mode,
                    **(extra or {}),
                },
            )
        except Exception:
            log.debug("approval audit failed (non-fatal)", exc_info=True)

    # --- config ----------------------------------------------------------------

    @staticmethod
    def get_lifecycle_config(agent_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        if agent_cfg is None:
            try:
                from flask import current_app, has_app_context

                agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
            except Exception:
                agent_cfg = {}
        cfg = dict((agent_cfg or {}).get("approval_lifecycle") or {})
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "legacy_approval_confirmed_enabled": bool(cfg.get("legacy_approval_confirmed_enabled", True)),
            "default_ttl_seconds": max(60, int(cfg.get("default_ttl_seconds") or 3600)),
            "grant_one_shot": bool(cfg.get("grant_one_shot", True)),
            "auto_approval_policy": dict(cfg.get("auto_approval_policy") or {}),
            "human_required_tools": [
                str(item or "").strip()
                for item in list(cfg.get("human_required_tools") or [])
                if str(item or "").strip()
            ],
            "goal_pre_approvals": dict(cfg.get("goal_pre_approvals") or {}),
        }

    # --- lifecycle --------------------------------------------------------------

    def create_pending_request(
        self,
        *,
        task_id: str | None,
        tool_name: str,
        arguments: dict[str, Any] | None,
        goal_id: str | None = None,
        tenant_id: str | None = None,
        project_id: str | None = None,
        organization_id: str | None = None,
        approval_intent_key: str | None = None,
        trace_id: str | None = None,
        target_fingerprint: str | None = None,
        risk_class: str = "unknown",
        k_class: str | None = None,
        governance_mode: str = "balanced",
        scope: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        agent_cfg: dict[str, Any] | None = None,
    ) -> ApprovalRequestDB:
        """Idempotent pending creation; supersedes stale pending requests.

        Auto-approval policy (ALWA-011) may grant the request immediately
        (decided_by ``auto_policy``) — never for ``human_required_tools``.
        """
        cfg = self.get_lifecycle_config(agent_cfg)
        canonical, payload, content_hash = canonicalize_tool_call(tool_name, arguments)
        digest = compute_arguments_digest(tool_name, canonical, target_fingerprint)
        ttl = int(ttl_seconds or cfg["default_ttl_seconds"])
        now = time.time()

        clean_scope = dict(scope or {})
        for forbidden in ("prompt", "raw_messages", "raw_response", "content", "unified_diff", "file_content"):
            clean_scope.pop(forbidden, None)

        with Session(_engine()) as session:
            normalized_intent = str(approval_intent_key or "").strip().lower() or None
            if normalized_intent is not None and (
                len(normalized_intent) != 64 or any(char not in "0123456789abcdef" for char in normalized_intent)
            ):
                raise ValueError("approval_intent_key_invalid")
            if normalized_intent:
                intent_match = session.exec(
                    select(ApprovalRequestDB).where(ApprovalRequestDB.approval_intent_key == normalized_intent)
                ).one_or_none()
                if intent_match is not None:
                    return intent_match
            existing = session.exec(
                select(ApprovalRequestDB)
                .where(ApprovalRequestDB.tool_name == tool_name)
                .where(ApprovalRequestDB.arguments_digest == digest)
                .where(ApprovalRequestDB.status.in_(("pending", "granted")))  # type: ignore[attr-defined]
            ).all()
            for row in existing:
                same_task = (row.task_id or None) == (task_id or None)
                if same_task and (row.expires_at is None or row.expires_at >= now):
                    return row

            stale = session.exec(
                select(ApprovalRequestDB)
                .where(ApprovalRequestDB.task_id == task_id)
                .where(ApprovalRequestDB.tool_name == tool_name)
                .where(ApprovalRequestDB.status == "pending")
            ).all()
            for row in stale:
                if row.arguments_digest != digest:
                    row.status = "superseded"
                    row.decided_at = now
                    row.decision_reason = "superseded_by_new_request"
                    session.add(row)
                    self._audit(AUDIT_APPROVAL_REQUEST_SUPERSEDED, row)

            content_ref = None
            if payload and content_hash:
                content_ref = self._store_content_payload(payload, content_hash)

            request = ApprovalRequestDB(
                id=str(uuid.uuid4()),
                task_id=task_id,
                goal_id=goal_id,
                tenant_id=str(tenant_id or "").strip() or None,
                project_id=str(project_id or "").strip() or None,
                organization_id=str(organization_id or "").strip() or None,
                approval_intent_key=normalized_intent,
                trace_id=trace_id,
                tool_name=str(tool_name).strip(),
                canonical_arguments=canonical,
                content_artifact_ref=content_ref,
                content_hash=content_hash,
                arguments_digest=digest,
                target_fingerprint=target_fingerprint,
                k_class=k_class,
                risk_class=str(risk_class or "unknown"),
                governance_mode=str(governance_mode or "balanced"),
                status="pending",
                scope=clean_scope,
                created_at=now,
                expires_at=now + ttl,
            )

            auto_reason = self._auto_approval_reason(
                cfg=cfg, tool_name=tool_name, scope=clean_scope, governance_mode=governance_mode
            )
            if auto_reason:
                request.status = "granted"
                request.decided_at = now
                request.decided_by = "auto_policy"
                request.decision_reason = auto_reason

            session.add(request)
            session.commit()
            session.refresh(request)
        self._audit(
            AUDIT_APPROVAL_REQUEST_CREATED, request, {"auto_granted": bool(request.decided_by == "auto_policy")}
        )
        return request

    def _auto_approval_reason(
        self,
        *,
        cfg: dict[str, Any],
        tool_name: str,
        scope: dict[str, Any],
        governance_mode: str,
    ) -> str | None:
        """Delegate automatic approval to the Hub-owned policy."""
        return self._auto_grant_policy.reason(
            policy_by_mode=dict(cfg.get("auto_approval_policy") or {}),
            human_required_tools=list(cfg.get("human_required_tools") or []),
            tool_name=tool_name,
            scope=scope,
            governance_mode=governance_mode,
        )

    def get_request(self, request_id: str) -> ApprovalRequestDB | None:
        with Session(_engine()) as session:
            return session.get(ApprovalRequestDB, str(request_id or ""))

    def list_requests(
        self,
        *,
        status: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        tool_name: str | None = None,
        tenant_id: str | None = None,
        project_id: str | None = None,
        organization_id: str | None = None,
        organization_ids: Collection[str] | None = None,
        scope_is_admin: bool = False,
        scope_team_id: str | None = None,
        before_created_at: float | None = None,
        before_id: str | None = None,
        limit: int = 200,
    ) -> list[ApprovalRequestDB]:
        with Session(_engine()) as session:
            statement = select(ApprovalRequestDB).order_by(
                ApprovalRequestDB.created_at.desc(),  # type: ignore[attr-defined]
                ApprovalRequestDB.id.desc(),  # type: ignore[attr-defined]
            )
            if status:
                statement = statement.where(ApprovalRequestDB.status == status)
            if task_id:
                statement = statement.where(ApprovalRequestDB.task_id == task_id)
            if goal_id:
                statement = statement.where(ApprovalRequestDB.goal_id == goal_id)
            if tool_name:
                statement = statement.where(ApprovalRequestDB.tool_name == tool_name)
            if tenant_id:
                statement = statement.where(
                    ApprovalRequestDB.tenant_id == str(tenant_id)
                )
            if project_id:
                statement = statement.where(
                    ApprovalRequestDB.project_id == str(project_id)
                )
            if organization_id:
                statement = statement.where(ApprovalRequestDB.organization_id == str(organization_id))
            if organization_ids is not None:
                allowed_organizations = tuple(
                    sorted({str(value or "").strip() for value in organization_ids if str(value or "").strip()})
                )
                unscoped_visible = self._unscoped_visibility_clause(
                    is_admin=scope_is_admin,
                    team_id=str(scope_team_id or "").strip(),
                    tenant_id=str(tenant_id or "").strip(),
                    project_id=str(project_id or "").strip(),
                )
                organization_visible = ApprovalRequestDB.organization_id.in_(
                    allowed_organizations
                )
                statement = statement.where(
                    or_(organization_visible, unscoped_visible)
                )
            if before_created_at is not None:
                if not before_id:
                    raise ValueError("approval_cursor_binding_invalid")
                statement = statement.where(
                    or_(
                        ApprovalRequestDB.created_at < float(before_created_at),
                        (
                            (ApprovalRequestDB.created_at == float(before_created_at))
                            & (ApprovalRequestDB.id < str(before_id))
                        ),
                    )
                )
            return list(
                session.exec(
                    statement.limit(max(1, min(int(limit), 1000)))
                ).all()
            )

    @staticmethod
    def _unscoped_visibility_clause(
        *,
        is_admin: bool,
        team_id: str,
        tenant_id: str,
        project_id: str,
    ):
        organization_unset = ApprovalRequestDB.organization_id.is_(None)
        tenant_boundary = (
            ApprovalRequestDB.tenant_id == tenant_id
            if tenant_id
            else ApprovalRequestDB.tenant_id.is_(None)
        )
        project_boundary = (
            ApprovalRequestDB.project_id == project_id
            if project_id
            else True
        )
        if is_admin:
            return and_(organization_unset, tenant_boundary, project_boundary)
        if not tenant_id:
            return False
        goal_team_visible = or_(
            GoalDB.team_id.is_(None),
            GoalDB.team_id == "",
            GoalDB.team_id == team_id if team_id else False,
        )
        direct_goal_visible = exists(
            select(GoalDB.id).where(
                GoalDB.id == ApprovalRequestDB.goal_id,
                goal_team_visible,
            )
        )
        task_goal_visible = exists(
            select(TaskDB.id).where(
                TaskDB.id == ApprovalRequestDB.task_id,
                TaskDB.goal_id.in_(select(GoalDB.id).where(goal_team_visible)),
            )
        )
        direct_task_visible = (
            exists(
                select(TaskDB.id).where(
                    TaskDB.id == ApprovalRequestDB.task_id,
                    TaskDB.goal_id.is_(None),
                    TaskDB.team_id == team_id,
                )
            )
            if team_id
            else False
        )
        return and_(
            organization_unset,
            tenant_boundary,
            project_boundary,
            or_(
                direct_goal_visible,
                and_(
                    ApprovalRequestDB.goal_id.is_(None),
                    or_(task_goal_visible, direct_task_visible),
                ),
            ),
        )

    def decide_request(
        self,
        request_id: str,
        *,
        decision: str,
        decided_by: str,
        reason: str | None = None,
        expires_at: float | None = None,
    ) -> ApprovalRequestDB:
        """Operator decision: only granted|denied, only for pending requests."""
        decision_value = str(decision or "").strip().lower()
        if decision_value not in {"granted", "denied"}:
            raise ApprovalDecisionError("invalid_decision", 400)
        with Session(_engine()) as session:
            request = session.get(ApprovalRequestDB, str(request_id or ""))
            if request is None:
                raise ApprovalDecisionError("request_not_found", 404)
            now = time.time()
            if request.status == "pending" and request.expires_at is not None and request.expires_at < now:
                transition = session.exec(
                    sa_update(ApprovalRequestDB)
                    .where(ApprovalRequestDB.id == str(request_id or ""))
                    .where(ApprovalRequestDB.status == "pending")
                    .values(status="expired")
                )
                if int(getattr(transition, "rowcount", 0) or 0) != 1:
                    session.rollback()
                    raise ApprovalDecisionError(
                        "request_transition_conflict",
                        409,
                    )
                session.commit()
                request = session.get(
                    ApprovalRequestDB,
                    str(request_id or ""),
                )
                if request is None:
                    raise ApprovalDecisionError(
                        "request_not_found",
                        404,
                    )
                session.refresh(request)
                self._audit(AUDIT_APPROVAL_REQUEST_EXPIRED, request)
                raise ApprovalDecisionError("request_expired", 409)
            if request.status != "pending":
                raise ApprovalDecisionError(f"request_already_{request.status}", 409)
            decision_expires_at = request.expires_at
            if expires_at is not None:
                try:
                    override = float(expires_at)
                except (TypeError, ValueError):
                    raise ApprovalDecisionError("invalid_expires_at", 400)
                max_override = now + 7 * 24 * 3600
                if override <= now or override > max_override:
                    raise ApprovalDecisionError("expires_at_out_of_range", 400)
                decision_expires_at = override
            transition = session.exec(
                sa_update(ApprovalRequestDB)
                .where(ApprovalRequestDB.id == str(request_id or ""))
                .where(ApprovalRequestDB.status == "pending")
                .values(
                    status=decision_value,
                    decided_at=now,
                    decided_by=str(decided_by or "operator"),
                    decision_reason=str(reason or "")[:500] or None,
                    expires_at=decision_expires_at,
                )
            )
            if int(getattr(transition, "rowcount", 0) or 0) != 1:
                session.rollback()
                raise ApprovalDecisionError("request_transition_conflict", 409)
            session.commit()
            request = session.get(ApprovalRequestDB, str(request_id or ""))
            if request is None:
                raise ApprovalDecisionError("request_not_found", 404)
            session.refresh(request)
        self._audit(AUDIT_APPROVAL_REQUEST_DECIDED, request, {"decision": decision_value})
        if decision_value == "granted":
            self._redispatch_task_after_grant(request)
        # Domain-specific post-decision effects remain Hub-owned and are
        # selected by exact tool name. Generic approvals keep their existing
        # lifecycle and never gain an implicit execution path.
        domain_outcome: dict[str, Any] = {}
        try:
            from agent.services.approval_decision_dispatcher_service import (
                get_approval_decision_dispatcher_service,
            )

            domain_outcome = get_approval_decision_dispatcher_service().dispatch(request) or {}
        except Exception as exc:
            log.exception("approval decision dispatch failed")
            domain_outcome = {
                "status": "failed",
                "reason_code": "approval_decision_dispatch_failed",
                "error_type": type(exc).__name__,
            }

        if str(domain_outcome.get("status") or "") == "ignored":
            return request

        failed = str(domain_outcome.get("status") or "") == "failed"
        request = (
            self._persist_domain_outcome(
                request_id=request.id,
                outcome=domain_outcome,
                # Recovery grants are durable outbox entries.  Reverting such a
                # grant to pending after an interrupted side effect loses the only
                # deterministic replay marker.
                restore_pending=(
                    failed
                    and decision_value == "granted"
                    and str(request.tool_name or "") != "planning.recovery_plan.materialize"
                ),
            )
            or request
        )
        if failed:
            self._audit(
                AUDIT_APPROVAL_DOMAIN_ACTION_FAILED,
                request,
                {
                    "reason_code": str(domain_outcome.get("reason_code") or "approval_domain_action_failed")[:160],
                },
            )
            raise ApprovalDecisionError(
                str(domain_outcome.get("reason_code") or "approval_domain_action_failed")[:160],
                409,
            )
        return request

    @staticmethod
    def _bounded_domain_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in (
            "status",
            "reason_code",
            "plan_id",
            "approval_request_id",
            "plan_digest",
        ):
            value = str(outcome.get(key) or "").strip()
            if value:
                result[key] = value[:256]
        node_count = outcome.get("node_count")
        if isinstance(node_count, int) and not isinstance(node_count, bool):
            result["node_count"] = max(0, min(node_count, 10_000))
        created = outcome.get("created_task_ids")
        if isinstance(created, list):
            result["created_task_ids"] = [str(value)[:160] for value in created[:256] if str(value).strip()]
        return result

    def _persist_domain_outcome(
        self,
        *,
        request_id: str,
        outcome: dict[str, Any],
        restore_pending: bool,
    ) -> ApprovalRequestDB | None:
        """Persist a bounded handler result and keep failed actions retryable."""
        with Session(_engine()) as session:
            request = session.get(ApprovalRequestDB, str(request_id or ""))
            if request is None:
                return None
            next_scope = {
                **dict(request.scope or {}),
                "decision_outcome": self._bounded_domain_outcome(outcome),
            }
            session.exec(
                sa_update(ApprovalRequestDB)
                .where(ApprovalRequestDB.id == str(request_id or ""))
                .values(scope=next_scope)
            )
            if restore_pending:
                # Never revive a concurrently consumed or expired grant.
                session.exec(
                    sa_update(ApprovalRequestDB)
                    .where(ApprovalRequestDB.id == str(request_id or ""))
                    .where(ApprovalRequestDB.status == "granted")
                    .values(
                        status="pending",
                        decided_at=None,
                        decided_by=None,
                        decision_reason=None,
                    )
                )
            session.commit()
            request = session.get(
                ApprovalRequestDB,
                str(request_id or ""),
            )
            if request is None:
                return None
            session.refresh(request)
            return request

    def reconcile_granted_domain_actions(
        self,
        *,
        limit: int = 64,
    ) -> dict[str, int]:
        """Resume durable recovery effects after a Hub interruption.

        The approval row is the outbox marker: ``granted`` means the exact
        action still needs dispatch, while a ``consumed`` recovery without a
        persisted domain outcome may still need its paused DAG released.
        """

        from agent.services.approval_decision_dispatcher_service import (
            get_approval_decision_dispatcher_service,
        )
        from agent.services.task_recovery_planning_service import (
            RECOVERY_MATERIALIZE_TOOL,
        )

        bounded_limit = max(1, min(int(limit), 256))
        candidates = self.list_requests(
            status="granted",
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            limit=bounded_limit,
        )
        if len(candidates) < bounded_limit:
            consumed = self.list_requests(
                status="consumed",
                tool_name=RECOVERY_MATERIALIZE_TOOL,
                limit=bounded_limit - len(candidates),
            )
            candidates.extend(
                row
                for row in consumed
                if (
                    not dict(row.scope or {}).get("decision_outcome")
                    or str(dict(dict(row.scope or {}).get("decision_outcome") or {}).get("status") or "") == "failed"
                )
            )
        if len(candidates) < bounded_limit:
            denied = self.list_requests(
                status="denied",
                tool_name=RECOVERY_MATERIALIZE_TOOL,
                limit=bounded_limit - len(candidates),
            )
            candidates.extend(
                row
                for row in denied
                if (
                    not dict(row.scope or {}).get("decision_outcome")
                    or str(dict(dict(row.scope or {}).get("decision_outcome") or {}).get("status") or "") == "failed"
                )
            )

        dispatcher = get_approval_decision_dispatcher_service()
        counts = {
            "examined": 0,
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
        }
        for request in candidates[:bounded_limit]:
            if str(request.tool_name or "") != RECOVERY_MATERIALIZE_TOOL:
                continue
            counts["examined"] += 1
            outcome = dispatcher.dispatch(request) or {}
            status = str(outcome.get("status") or "")
            reason_code = str(outcome.get("reason_code") or "")
            if status == "ignored" and reason_code == ("recovery_action_in_progress"):
                counts["in_progress"] += 1
                continue
            if status == "ignored":
                continue
            self._persist_domain_outcome(
                request_id=request.id,
                outcome=outcome,
                restore_pending=False,
            )
            if status == "failed":
                counts["failed"] += 1
            else:
                counts["completed"] += 1
        return counts

    def _redispatch_task_after_grant(self, request: ApprovalRequestDB) -> None:
        """ALWA-008: put a pending_approval task back into the dispatch flow."""
        if str(request.tool_name or "") in (PASSIVE_PLANNING_APPROVAL_TOOLS | {"planning.recovery_plan.materialize"}):
            return
        task_id = str(request.task_id or "").strip()
        if not task_id:
            return
        try:
            from agent.services.repository_registry import get_repository_registry

            task_repo = get_repository_registry().task_repo
            task = task_repo.get_by_id(task_id)
            if task is None:
                return
            from agent.services.recovery_task_mutation_policy import (
                recovery_task_role,
            )

            if recovery_task_role(task) is not None:
                return
            if str(getattr(task, "status", "") or "") in {"pending_approval", "blocked_pending_approval", "blocked"}:
                task.status = "todo"
                task.status_reason_code = "approval_granted_redispatch"
                task_repo.save(task)
                self._audit(
                    AUDIT_APPROVAL_REQUEST_REDISPATCH,
                    request,
                    {"redispatched_task_status": "todo"},
                )
        except Exception:
            log.warning("redispatch after grant failed (non-fatal)", exc_info=True)

    def resolve_grant_for_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        task_id: str | None = None,
        goal_id: str | None = None,
        target_fingerprint: str | None = None,
    ) -> ApprovalRequestDB | None:
        """Return a valid grant for exactly this canonicalized call, else None.

        Valid means: status=granted, not expired, digest exact match, and
        the request is scoped to this task (or goal-scoped pre-approval).
        """
        canonical, _, _ = canonicalize_tool_call(tool_name, arguments)
        digest = compute_arguments_digest(tool_name, canonical, target_fingerprint)
        now = time.time()
        with Session(_engine()) as session:
            rows = session.exec(
                select(ApprovalRequestDB)
                .where(ApprovalRequestDB.tool_name == str(tool_name or "").strip())
                .where(ApprovalRequestDB.arguments_digest == digest)
                .where(ApprovalRequestDB.status == "granted")
            ).all()
        for row in rows:
            if row.expires_at is not None and row.expires_at < now:
                continue
            if row.task_id and task_id and row.task_id != task_id:
                continue
            if row.task_id and not task_id:
                continue
            if not row.task_id and row.goal_id and row.goal_id != (goal_id or ""):
                continue
            return row
        return None

    def resolve_goal_pre_approval(self, *, goal_id: str | None, tool_name: str) -> ApprovalRequestDB | None:
        """Goal-scoped pre-approval (tool-class level, digest-free by design).

        Pre-approvals are the only non-digest grants; they are restricted to
        the configured tool list at goal start and never cover
        human_required tools (enforced at creation).
        """
        if not goal_id:
            return None
        now = time.time()
        with Session(_engine()) as session:
            rows = session.exec(
                select(ApprovalRequestDB)
                .where(ApprovalRequestDB.goal_id == str(goal_id))
                .where(ApprovalRequestDB.tool_name == str(tool_name or "").strip())
                .where(ApprovalRequestDB.status == "granted")
            ).all()
        for row in rows:
            if not bool((row.scope or {}).get("pre_approval")):
                continue
            if row.expires_at is not None and row.expires_at < now:
                continue
            return row
        return None

    def consume_request(self, request_id: str) -> ApprovalRequestDB | None:
        with Session(_engine()) as session:
            request = session.get(ApprovalRequestDB, str(request_id or ""))
            if request is None:
                return None
            if request.status == "consumed":
                return request
            if request.status != "granted":
                return None
            transition = session.exec(
                sa_update(ApprovalRequestDB)
                .where(ApprovalRequestDB.id == str(request_id or ""))
                .where(ApprovalRequestDB.status == "granted")
                .values(
                    status="consumed",
                    consumed_at=time.time(),
                )
            )
            if int(getattr(transition, "rowcount", 0) or 0) != 1:
                session.rollback()
                current = session.get(
                    ApprovalRequestDB,
                    str(request_id or ""),
                )
                return current if current is not None and current.status == "consumed" else None
            session.commit()
            request = session.get(
                ApprovalRequestDB,
                str(request_id or ""),
            )
            if request is None:
                return None
            session.refresh(request)
        self._audit(AUDIT_APPROVAL_REQUEST_CONSUMED, request)
        return request

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
            self._validate_passive_request_binding(
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
            self._validate_passive_request_binding(
                authoritative,
                canonical_arguments=canonical,
                arguments_digest=arguments_digest,
                **bindings,
            )
            return authoritative

    @staticmethod
    def _validate_passive_request_binding(
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

    def expire_old_requests(self) -> int:
        now = time.time()
        expired_rows: list[ApprovalRequestDB] = []
        with Session(_engine()) as session:
            rows = session.exec(
                select(ApprovalRequestDB).where(ApprovalRequestDB.status.in_(("pending", "granted")))  # type: ignore[attr-defined]
            ).all()
            for row in rows:
                if row.expires_at is None or row.expires_at >= now:
                    continue
                if row.status == "granted" and str(row.tool_name or "") == "planning.recovery_plan.materialize":
                    # A granted recovery is a durable outbox item, even when
                    # dispatch resumes after its original operator TTL.
                    continue
                previous_status = str(row.status or "")
                transition = session.exec(
                    sa_update(ApprovalRequestDB)
                    .where(ApprovalRequestDB.id == row.id)
                    .where(ApprovalRequestDB.status == previous_status)
                    .where(ApprovalRequestDB.status.in_(("pending", "granted")))
                    .values(status="expired")
                )
                if int(getattr(transition, "rowcount", 0) or 0) == 1:
                    row.status = "expired"
                    expired_rows.append(row)
            session.commit()
        for row in expired_rows:
            self._audit(AUDIT_APPROVAL_REQUEST_EXPIRED, row)
        return len(expired_rows)

    # --- goal-level pre-approvals (ALWA-011) -----------------------------------

    def create_goal_pre_approvals(
        self,
        *,
        goal_id: str,
        agent_cfg: dict[str, Any] | None = None,
        governance_mode: str = "balanced",
    ) -> list[ApprovalRequestDB]:
        cfg = self.get_lifecycle_config(agent_cfg)
        pre_cfg = dict(cfg.get("goal_pre_approvals") or {})
        if not bool(pre_cfg.get("enabled", False)):
            return []
        ttl = max(60, int(pre_cfg.get("ttl_seconds") or 7200))
        human_required = set(cfg.get("human_required_tools") or [])
        created: list[ApprovalRequestDB] = []
        now = time.time()
        for tool_name in [
            str(item or "").strip() for item in list(pre_cfg.get("tools") or []) if str(item or "").strip()
        ]:
            if tool_name in human_required:
                continue
            request = ApprovalRequestDB(
                id=str(uuid.uuid4()),
                task_id=None,
                goal_id=str(goal_id),
                tool_name=tool_name,
                canonical_arguments={},
                arguments_digest=compute_arguments_digest(tool_name, {"__pre_approval__": goal_id}),
                risk_class="execution",
                governance_mode=str(governance_mode or "balanced"),
                status="granted",
                scope={"pre_approval": True, "goal_id": str(goal_id), "approval_class": "goal_pre_approval"},
                created_at=now,
                expires_at=now + ttl,
                decided_at=now,
                decided_by="goal_pre_approval_policy",
                decision_reason="goal_level_pre_approval",
            )
            with Session(_engine()) as session:
                session.add(request)
                session.commit()
                session.refresh(request)
            self._audit(AUDIT_APPROVAL_REQUEST_CREATED, request, {"pre_approval": True})
            created.append(request)
        return created

    # --- deterministic re-execution (ALWA-008) -----------------------------------

    def execute_granted_tool_call(self, request_id: str, *, workspace_dir: str) -> dict[str, Any]:
        """Re-execute exactly the granted call without re-prompting the worker.

        Reconstructs the arguments from canonical_arguments, loads
        content-bearing payloads via content_artifact_ref (verified against
        content_hash) and executes through the regular tool executor. The
        grant is consumed afterwards (one-shot policy).
        """
        request = self.get_request(request_id)
        if request is None:
            raise ApprovalDecisionError("request_not_found", 404)
        if request.status != "granted":
            raise ApprovalDecisionError(f"request_not_granted:{request.status}", 409)
        if request.expires_at is not None and request.expires_at < time.time():
            raise ApprovalDecisionError("request_expired", 409)

        arguments = json.loads(json.dumps(request.canonical_arguments or {}))
        if request.content_artifact_ref:
            payload = self.load_content_payload(request.content_artifact_ref, request.content_hash)
            if payload is None:
                raise ApprovalDecisionError("content_payload_hash_mismatch", 409)
            for field, value in payload.items():
                arguments[field] = value

        from agent.services.tools import execute_ananta_tool

        result = execute_ananta_tool(
            tool_name=request.tool_name,
            arguments=arguments,
            workspace_dir=str(workspace_dir),
            tool_call_id=f"approved:{request.id[:8]}",
            config={},
        )
        cfg = self.get_lifecycle_config()
        if cfg.get("grant_one_shot", True):
            self.consume_request(request.id)
        return result


approval_request_service = ApprovalRequestService()


def get_approval_request_service() -> ApprovalRequestService:
    return approval_request_service
