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
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from agent.config import settings
from agent.db_models import ApprovalRequestDB
from agent.db_models.governance import APPROVAL_REQUEST_STATUSES

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


class ApprovalRequestService:
    """Lifecycle of digest-bound ApprovalRequests (hub side)."""

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
        path = self._payload_dir() / f"{ref[len(_PAYLOAD_REF_PREFIX):]}.json"
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
            "human_required_tools": [str(item or "").strip() for item in list(cfg.get("human_required_tools") or []) if str(item or "").strip()],
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

            auto_reason = self._auto_approval_reason(cfg=cfg, tool_name=tool_name, scope=clean_scope, governance_mode=governance_mode)
            if auto_reason:
                request.status = "granted"
                request.decided_at = now
                request.decided_by = "auto_policy"
                request.decision_reason = auto_reason

            session.add(request)
            session.commit()
            session.refresh(request)
        self._audit(AUDIT_APPROVAL_REQUEST_CREATED, request, {"auto_granted": bool(request.decided_by == "auto_policy")})
        return request

    @staticmethod
    def _auto_approval_reason(*, cfg: dict[str, Any], tool_name: str, scope: dict[str, Any], governance_mode: str) -> str | None:
        """ALWA-011: policy-driven auto approval; never for human_required tools."""
        name = str(tool_name or "").strip()
        if name in set(cfg.get("human_required_tools") or []):
            return None
        mode_policy = dict((cfg.get("auto_approval_policy") or {}).get(str(governance_mode or "balanced")) or {})
        approval_class = str(scope.get("approval_class") or "").strip()
        if approval_class == "read_only" and bool(mode_policy.get("read_only")):
            return "auto_approved:read_only"
        if approval_class == "controlled_workspace_writes" and bool(mode_policy.get("controlled_workspace_writes")):
            return "auto_approved:controlled_workspace_writes"
        if name == "test.run" and bool(mode_policy.get("test_run")):
            return "auto_approved:test_run"
        return None

    def get_request(self, request_id: str) -> ApprovalRequestDB | None:
        with Session(_engine()) as session:
            return session.get(ApprovalRequestDB, str(request_id or ""))

    def list_requests(
        self,
        *,
        status: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 200,
    ) -> list[ApprovalRequestDB]:
        with Session(_engine()) as session:
            statement = select(ApprovalRequestDB).order_by(ApprovalRequestDB.created_at.desc())  # type: ignore[attr-defined]
            if status:
                statement = statement.where(ApprovalRequestDB.status == status)
            if task_id:
                statement = statement.where(ApprovalRequestDB.task_id == task_id)
            if goal_id:
                statement = statement.where(ApprovalRequestDB.goal_id == goal_id)
            return list(session.exec(statement).all())[: max(1, min(int(limit), 1000))]

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
                request.status = "expired"
                session.add(request)
                session.commit()
                session.refresh(request)
                self._audit(AUDIT_APPROVAL_REQUEST_EXPIRED, request)
                raise ApprovalDecisionError("request_expired", 409)
            if request.status != "pending":
                raise ApprovalDecisionError(f"request_already_{request.status}", 409)
            request.status = decision_value
            request.decided_at = now
            request.decided_by = str(decided_by or "operator")
            request.decision_reason = str(reason or "")[:500] or None
            if expires_at is not None:
                try:
                    override = float(expires_at)
                except (TypeError, ValueError):
                    raise ApprovalDecisionError("invalid_expires_at", 400)
                max_override = now + 7 * 24 * 3600
                if override <= now or override > max_override:
                    raise ApprovalDecisionError("expires_at_out_of_range", 400)
                request.expires_at = override
            session.add(request)
            session.commit()
            session.refresh(request)
        self._audit(AUDIT_APPROVAL_REQUEST_DECIDED, request, {"decision": decision_value})
        if decision_value == "granted":
            self._redispatch_task_after_grant(request)
        return request

    def _redispatch_task_after_grant(self, request: ApprovalRequestDB) -> None:
        """ALWA-008: put a pending_approval task back into the dispatch flow."""
        task_id = str(request.task_id or "").strip()
        if not task_id:
            return
        try:
            from agent.services.repository_registry import get_repository_registry

            task_repo = get_repository_registry().task_repo
            task = task_repo.get_by_id(task_id)
            if task is None:
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
            if request is None or request.status != "granted":
                return None
            request.status = "consumed"
            request.consumed_at = time.time()
            session.add(request)
            session.commit()
            session.refresh(request)
        self._audit(AUDIT_APPROVAL_REQUEST_CONSUMED, request)
        return request

    def expire_old_requests(self) -> int:
        now = time.time()
        expired = 0
        with Session(_engine()) as session:
            rows = session.exec(
                select(ApprovalRequestDB).where(ApprovalRequestDB.status.in_(("pending", "granted")))  # type: ignore[attr-defined]
            ).all()
            for row in rows:
                if row.expires_at is not None and row.expires_at < now:
                    row.status = "expired"
                    session.add(row)
                    expired += 1
                    self._audit(AUDIT_APPROVAL_REQUEST_EXPIRED, row)
            session.commit()
        return expired

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
        for tool_name in [str(item or "").strip() for item in list(pre_cfg.get("tools") or []) if str(item or "").strip()]:
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
