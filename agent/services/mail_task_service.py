"""Hub-owned lifecycle, coalescing and leases for delegated mail operations."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

MAIL_TASK_SCHEMA = "ananta.mail_task.v1"
MAIL_TASK_RESULT_SCHEMA = "ananta.mail_task_result.v1"
MAIL_TASK_KIND = "mail_operation"
MAIL_OPERATIONS = frozenset(
    {"discovery", "sync", "body", "mutation", "migration", "cutover", "diagnose"}
)
MAIL_PROVIDERS = frozenset({"imap", "jmap"})
_ACTIVE_STATUSES = frozenset(
    {"created", "todo", "blocked", "blocked_by_dependency", "assigned", "in_progress", "running"}
)
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "idempotency_key",
        "operation",
        "status",
        "reason_code",
        "retryable",
        "retry_after_ms",
        "provider",
        "result_refs",
        "counters",
        "lease_fencing_token",
    }
)
_FORBIDDEN_KEY_PARTS = (
    "password",
    "secret",
    "authorization",
    "credential",
    "body",
    "content",
    "attachment",
    "blob",
    "message_text",
)
_SAFE_SUFFIXES = ("_ref", "_refs", "_hash", "_count", "_counts")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _safe_reference(value: str, *, field: str) -> str:
    candidate = str(value or "").strip()
    if _REFERENCE.fullmatch(candidate) is None:
        raise ValueError(f"mail_task_{field}_invalid")
    return candidate


def _contains_forbidden_data(value: Any, *, key: str = "") -> bool:
    normalized = str(key or "").strip().lower()
    if normalized and any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
        if not normalized.endswith(_SAFE_SUFFIXES):
            return True
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden_data(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_data(item) for item in value)
    return isinstance(value, (bytes, bytearray, memoryview))


@dataclass(frozen=True)
class MailWorkspaceScope:
    workspace_id: str
    tenant_id: str = ""

    def __post_init__(self) -> None:
        workspace_id = str(self.workspace_id or "").strip()
        tenant_id = str(self.tenant_id or "").strip()
        if _SCOPE_VALUE.fullmatch(workspace_id) is None:
            raise ValueError("mail_task_workspace_id_invalid")
        if tenant_id and _SCOPE_VALUE.fullmatch(tenant_id) is None:
            raise ValueError("mail_task_tenant_id_invalid")
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "tenant_id", tenant_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailWorkspaceScope":
        raw = dict(value or {})
        if set(raw) - {"workspace_id", "tenant_id"}:
            raise ValueError("mail_task_workspace_scope_fields_forbidden")
        return cls(
            workspace_id=str(raw.get("workspace_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        value = {"workspace_id": self.workspace_id}
        if self.tenant_id:
            value["tenant_id"] = self.tenant_id
        return value


@dataclass(frozen=True)
class MailAccountLease:
    job_id: str
    account_ref: str
    owner_ref: str
    fencing_token: int
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "account_ref": self.account_ref,
            "owner_ref": self.owner_ref,
            "fencing_token": self.fencing_token,
            "expires_at": self.expires_at,
        }


class MailTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class MailTaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any: ...

    def get_all(self) -> Sequence[Any]: ...


class MailAccountLeaseStorePort(Protocol):
    def claim(
        self,
        *,
        job_id: str,
        account_ref: str,
        owner_ref: str,
        ttl_seconds: int,
        now: float,
    ) -> MailAccountLease | None: ...

    def release(
        self,
        *,
        job_id: str,
        fencing_token: int,
        owner_ref: str | None,
        now: float,
    ) -> bool: ...


class InMemoryMailAccountLeaseStore:
    """Deterministic test seam; production uses the shared task database."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_account: dict[str, MailAccountLease] = {}
        self._last_fence: dict[str, int] = {}

    def claim(
        self,
        *,
        job_id: str,
        account_ref: str,
        owner_ref: str,
        ttl_seconds: int,
        now: float,
    ) -> MailAccountLease | None:
        with self._lock:
            current = self._by_account.get(account_ref)
            if current is not None and current.expires_at > now:
                if current.job_id != job_id or current.owner_ref != owner_ref:
                    return None
                renewed = MailAccountLease(
                    job_id=job_id,
                    account_ref=account_ref,
                    owner_ref=owner_ref,
                    fencing_token=current.fencing_token,
                    expires_at=now + ttl_seconds,
                )
                self._by_account[account_ref] = renewed
                return renewed
            fencing = self._last_fence.get(account_ref, 0) + 1
            self._last_fence[account_ref] = fencing
            lease = MailAccountLease(
                job_id=job_id,
                account_ref=account_ref,
                owner_ref=owner_ref,
                fencing_token=fencing,
                expires_at=now + ttl_seconds,
            )
            self._by_account[account_ref] = lease
            return lease

    def release(
        self,
        *,
        job_id: str,
        fencing_token: int,
        owner_ref: str | None,
        now: float,
    ) -> bool:
        del now
        with self._lock:
            current = next(
                (
                    lease
                    for lease in self._by_account.values()
                    if lease.job_id == job_id
                ),
                None,
            )
            if current is None or current.fencing_token != int(fencing_token):
                return False
            if owner_ref is not None and current.owner_ref != owner_ref:
                return False
            self._by_account.pop(current.account_ref, None)
            return True


class DatabaseMailAccountLeaseStore:
    """TaskDB-backed account lease with PostgreSQL row locks and SQLite fencing."""

    @staticmethod
    def _context(task: Any) -> dict[str, Any]:
        return dict(getattr(task, "worker_execution_context", {}) or {})

    @staticmethod
    def _envelope(task: Any) -> dict[str, Any]:
        return dict(DatabaseMailAccountLeaseStore._context(task).get("mail_task") or {})

    @staticmethod
    def _lease(task: Any) -> dict[str, Any]:
        return dict(
            DatabaseMailAccountLeaseStore._context(task)
            .get("mail_task_control", {})
            .get("lease", {})
            or {}
        )

    @staticmethod
    def _locked_rows(session):
        from sqlmodel import select

        from agent.db_models import TaskDB

        statement = select(TaskDB).where(TaskDB.task_kind == MAIL_TASK_KIND)
        bind = session.get_bind()
        if bind is not None and bind.dialect.name != "sqlite":
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _begin_sqlite_write(session) -> None:
        from sqlalchemy import text

        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            session.exec(text("BEGIN IMMEDIATE"))

    def claim(
        self,
        *,
        job_id: str,
        account_ref: str,
        owner_ref: str,
        ttl_seconds: int,
        now: float,
    ) -> MailAccountLease | None:
        from sqlmodel import Session

        from agent.database import engine

        with Session(engine) as session:
            self._begin_sqlite_write(session)
            rows = self._locked_rows(session)
            target = next((row for row in rows if str(row.id) == str(job_id)), None)
            if target is None:
                session.rollback()
                raise ValueError("mail_task_not_found")
            if str(getattr(target, "status", "") or "").lower() in _TERMINAL_STATUSES:
                session.rollback()
                return None
            max_fence = 0
            current_target: dict[str, Any] = {}
            for row in rows:
                envelope = self._envelope(row)
                if str(envelope.get("account_ref") or "") != account_ref:
                    continue
                lease = self._lease(row)
                max_fence = max(max_fence, int(lease.get("fencing_token") or 0))
                if str(row.id) == str(job_id):
                    current_target = lease
                    continue
                if float(lease.get("expires_at") or 0.0) > now:
                    session.rollback()
                    return None
            if (
                current_target
                and float(current_target.get("expires_at") or 0.0) > now
                and str(current_target.get("owner_ref") or "") == owner_ref
            ):
                fencing = int(current_target.get("fencing_token") or 0)
            else:
                fencing = max_fence + 1
            lease = MailAccountLease(
                job_id=str(job_id),
                account_ref=account_ref,
                owner_ref=owner_ref,
                fencing_token=fencing,
                expires_at=now + ttl_seconds,
            )
            context = self._context(target)
            control = dict(context.get("mail_task_control") or {})
            control["lease"] = lease.to_dict()
            context["mail_task_control"] = control
            target.worker_execution_context = context
            target.updated_at = now
            session.add(target)
            session.commit()
            return lease

    def release(
        self,
        *,
        job_id: str,
        fencing_token: int,
        owner_ref: str | None,
        now: float,
    ) -> bool:
        from sqlmodel import Session

        from agent.database import engine

        with Session(engine) as session:
            self._begin_sqlite_write(session)
            rows = self._locked_rows(session)
            target = next((row for row in rows if str(row.id) == str(job_id)), None)
            if target is None:
                session.rollback()
                return False
            current = self._lease(target)
            if int(current.get("fencing_token") or 0) != int(fencing_token):
                session.rollback()
                return False
            if owner_ref is not None and str(current.get("owner_ref") or "") != owner_ref:
                session.rollback()
                return False
            current["expires_at"] = now
            current["released_at"] = now
            context = self._context(target)
            control = dict(context.get("mail_task_control") or {})
            control["lease"] = current
            context["mail_task_control"] = control
            target.worker_execution_context = context
            target.updated_at = now
            session.add(target)
            session.commit()
            return True


class MailTaskService:
    """Central control-plane owner for mail task queueing and lifecycle."""

    def __init__(
        self,
        *,
        task_queue: MailTaskQueuePort | None = None,
        task_repository: MailTaskRepositoryPort | None = None,
        lease_store: MailAccountLeaseStorePort | None = None,
        status_updater: Callable[..., Any] | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        role: str | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._task_repository = task_repository
        self._lease_store = lease_store
        self._status_updater = status_updater
        self._audit = audit or self._default_audit
        self._clock = clock
        self._role = role
        self._lock = threading.RLock()

    @staticmethod
    def _default_audit(event: str, payload: dict[str, Any]) -> None:
        from agent.common.audit import log_audit

        log_audit(event, payload)

    def _require_hub(self) -> None:
        if self._role is None:
            from agent.config import settings

            role = settings.role
        else:
            role = self._role
        if str(role or "").strip().lower() != "hub":
            raise PermissionError("mail_task_hub_role_required")

    def _queue(self) -> MailTaskQueuePort:
        if self._task_queue is not None:
            return self._task_queue
        from agent.services.task_queue_service import get_task_queue_service

        return get_task_queue_service()

    def _repository(self) -> MailTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def _leases(self) -> MailAccountLeaseStorePort:
        if self._lease_store is not None:
            return self._lease_store
        return DatabaseMailAccountLeaseStore()

    def _update_status(self, task_id: str, status: str, **kwargs: Any) -> Any:
        if self._status_updater is not None:
            return self._status_updater(task_id, status, **kwargs)
        from agent.services.task_runtime_service import update_local_task_status

        return update_local_task_status(task_id, status, **kwargs)

    @staticmethod
    def _raw(task: Any) -> dict[str, Any]:
        if task is None:
            return {}
        if hasattr(task, "model_dump"):
            return dict(task.model_dump())
        if isinstance(task, Mapping):
            return dict(task)
        return {
            field: getattr(task, field, None)
            for field in (
                "id",
                "status",
                "worker_execution_context",
                "verification_status",
                "status_reason_code",
            )
        }

    @staticmethod
    def _envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
        context = raw.get("worker_execution_context")
        if not isinstance(context, Mapping):
            return {}
        value = context.get("mail_task")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _status(raw_status: Any) -> str:
        return {
            "created": "queued",
            "todo": "queued",
            "blocked": "queued",
            "blocked_by_dependency": "queued",
            "assigned": "running",
            "in_progress": "running",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(str(raw_status or "todo").strip().lower(), "queued")

    @staticmethod
    def _normalize_policy_refs(value: Mapping[str, Any] | None) -> dict[str, str]:
        raw = dict(value or {})
        if not raw:
            raise ValueError("mail_task_policy_ref_required")
        normalized: dict[str, str] = {}
        for key, item in raw.items():
            field = str(key or "").strip().lower()
            if (
                not field.endswith("_ref")
                or not field.replace("_", "").isalnum()
                or "credential" in field
            ):
                raise ValueError("mail_task_policy_ref_field_invalid")
            normalized[field] = _safe_reference(str(item or ""), field=field)
        if _contains_forbidden_data(normalized):
            raise ValueError("mail_task_sensitive_payload_forbidden")
        return normalized

    @staticmethod
    def _normalize_operation_refs(
        value: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        raw = dict(value or {})
        normalized: dict[str, str] = {}
        for key, item in raw.items():
            field = str(key or "").strip().lower()
            if (
                not field.endswith("_ref")
                or not field.replace("_", "").isalnum()
                or "credential" in field
                or "secret" in field
            ):
                raise ValueError("mail_task_operation_ref_field_invalid")
            normalized[field] = _safe_reference(str(item or ""), field=field)
        if _contains_forbidden_data(normalized):
            raise ValueError("mail_task_sensitive_payload_forbidden")
        return normalized

    def get_task(self, job_id: str) -> dict[str, Any] | None:
        raw = self._raw(self._repository().get_by_id(str(job_id)))
        envelope = self._envelope(raw)
        if envelope.get("schema") != MAIL_TASK_SCHEMA:
            return None
        verification = dict(raw.get("verification_status") or {})
        result = verification.get("mail_task_result")
        control = dict(
            dict(raw.get("worker_execution_context") or {}).get("mail_task_control")
            or {}
        )
        lease = dict(control.get("lease") or {})
        view: dict[str, Any] = {
            "job_id": envelope.get("job_id"),
            "operation": envelope.get("operation"),
            "account_ref": envelope.get("account_ref"),
            "workspace_scope": _clone(envelope.get("workspace_scope") or {}),
            "idempotency_key": envelope.get("idempotency_key"),
            "request_fingerprint": envelope.get("request_fingerprint"),
            "operation_refs": _clone(envelope.get("operation_refs") or {}),
            "policy_refs": _clone(envelope.get("policy_refs") or {}),
            "deadline_at": envelope.get("deadline_at"),
            "max_attempts": envelope.get("max_attempts"),
            "status": self._status(raw.get("status")),
            "created_at": envelope.get("created_at"),
            "lease": (
                {
                    "fencing_token": lease.get("fencing_token"),
                    "expires_at": lease.get("expires_at"),
                }
                if lease
                else None
            ),
        }
        if isinstance(result, Mapping):
            view["result"] = _clone(dict(result))
        return {key: value for key, value in view.items() if value is not None}

    def _active_for_account(
        self,
        account_ref: str,
        *,
        operation: str | None = None,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for task in self._repository().get_all():
            raw = self._raw(task)
            if str(raw.get("status") or "").strip().lower() not in _ACTIVE_STATUSES:
                continue
            envelope = self._envelope(raw)
            if envelope.get("schema") != MAIL_TASK_SCHEMA:
                continue
            if str(envelope.get("account_ref") or "") != account_ref:
                continue
            if operation and str(envelope.get("operation") or "") != operation:
                continue
            matches.append(raw)
        return matches

    def submit(
        self,
        *,
        operation: str,
        account_ref: str,
        workspace_scope: MailWorkspaceScope | Mapping[str, Any],
        idempotency_key: str,
        policy_refs: Mapping[str, Any],
        actor: str,
        operation_refs: Mapping[str, Any] | None = None,
        priority: str | None = None,
        deadline_seconds: int = 300,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        self._require_hub()
        normalized_operation = str(operation or "").strip().lower()
        if normalized_operation not in MAIL_OPERATIONS:
            raise ValueError("mail_task_operation_invalid")
        account = _safe_reference(account_ref, field="account_ref")
        scope = (
            workspace_scope
            if isinstance(workspace_scope, MailWorkspaceScope)
            else MailWorkspaceScope.from_mapping(workspace_scope)
        )
        key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise ValueError("mail_task_idempotency_key_invalid")
        refs = self._normalize_policy_refs(policy_refs)
        operation_reference_values = self._normalize_operation_refs(operation_refs)
        if (
            normalized_operation in {"body", "mutation", "migration", "cutover"}
            and not operation_reference_values
        ):
            raise ValueError("mail_task_operation_ref_required")
        deadline = int(deadline_seconds)
        attempts = int(max_attempts)
        if deadline < 5 or deadline > 86400:
            raise ValueError("mail_task_deadline_invalid")
        if attempts < 1 or attempts > 5:
            raise ValueError("mail_task_retry_budget_invalid")
        normalized_priority = str(
            priority
            or {
                "cutover": "critical",
                "mutation": "high",
                "body": "high",
                "discovery": "medium",
                "migration": "medium",
                "sync": "low",
                "diagnose": "low",
            }[normalized_operation]
        ).strip().lower()
        if normalized_priority not in {"low", "medium", "high", "critical"}:
            raise ValueError("mail_task_priority_invalid")
        intent = {
            "operation": normalized_operation,
            "account_ref": account,
            "workspace_scope": scope.to_dict(),
            "idempotency_key": key,
            "operation_refs": operation_reference_values,
            "policy_refs": refs,
        }
        request_fingerprint = _digest(intent)
        job_id = "mail-task-" + _digest(
            {
                "account_ref": account,
                "workspace_scope": scope.to_dict(),
                "idempotency_key": key,
            }
        )[:32]
        with self._lock:
            existing = self.get_task(job_id)
            if existing is not None:
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise RuntimeError("mail_task_idempotency_mismatch")
                return existing
            if normalized_operation == "sync":
                active_sync = self._active_for_account(account, operation="sync")
                if active_sync:
                    coalesced = self.get_task(
                        str(self._envelope(active_sync[0]).get("job_id") or "")
                    )
                    if coalesced is not None:
                        return {**coalesced, "coalesced": True}
            now = float(self._clock())
            envelope = {
                "schema": MAIL_TASK_SCHEMA,
                "job_id": job_id,
                "operation": normalized_operation,
                "account_ref": account,
                "workspace_scope": scope.to_dict(),
                "idempotency_key": key,
                "request_fingerprint": request_fingerprint,
                "operation_refs": operation_reference_values,
                "policy_refs": refs,
                "deadline_at": now + deadline,
                "max_attempts": attempts,
                "created_at": now,
            }
            if _contains_forbidden_data(envelope):
                raise ValueError("mail_task_sensitive_payload_forbidden")
            self._queue().ingest_task(
                task_id=job_id,
                status="todo",
                title=f"Mail {normalized_operation}: {_digest(account)[:12]}",
                description=(
                    "Hub-owned, worker-delegated mail operation. "
                    "The envelope contains references only."
                ),
                priority=normalized_priority,
                created_by=str(actor or "mail-control"),
                source="mail_control",
                tags=["mail", "hub_delegated", "persistent_job", normalized_operation],
                event_type="task_ingested",
                event_channel="hub_task_queue",
                event_details={
                    "operation": normalized_operation,
                    "account_ref_hash": _digest(account),
                    "request_fingerprint": request_fingerprint,
                    "domain_event_type": "mail_task_queued",
                },
                extra_fields={
                    "task_kind": MAIL_TASK_KIND,
                    "required_context_scope": "mail",
                    "required_capabilities": ["mail", f"mail.{normalized_operation}"],
                    "worker_execution_context": {
                        "mail_task": envelope,
                        "mail_task_control": {"lease": None},
                    },
                    "verification_spec": {
                        "schema": MAIL_TASK_RESULT_SCHEMA,
                        "idempotency_key": key,
                    },
                },
            )
            created = self.get_task(job_id)
            if created is None:
                raise RuntimeError("mail_task_persistence_failed")
            self._audit_task("queued", envelope, actor)
            return created

    def claim_for_delegation(
        self,
        *,
        job_id: str,
        owner_ref: str,
        ttl_seconds: int = 60,
    ) -> dict[str, Any] | None:
        self._require_hub()
        task = self.get_task(job_id)
        if task is None:
            raise ValueError("mail_task_not_found")
        ttl = int(ttl_seconds)
        if ttl < 10 or ttl > 900:
            raise ValueError("mail_task_lease_ttl_invalid")
        lease = self._leases().claim(
            job_id=job_id,
            account_ref=str(task["account_ref"]),
            owner_ref=_safe_reference(owner_ref, field="lease_owner_ref"),
            ttl_seconds=ttl,
            now=float(self._clock()),
        )
        return lease.to_dict() if lease is not None else None

    def release_lease(
        self,
        *,
        job_id: str,
        fencing_token: int,
        owner_ref: str | None = None,
    ) -> bool:
        self._require_hub()
        return self._leases().release(
            job_id=job_id,
            fencing_token=int(fencing_token),
            owner_ref=owner_ref,
            now=float(self._clock()),
        )

    def cancel(self, *, job_id: str, actor: str) -> dict[str, Any]:
        self._require_hub()
        raw = self._raw(self._repository().get_by_id(job_id))
        envelope = self._envelope(raw)
        if not envelope:
            raise ValueError("mail_task_not_found")
        if str(raw.get("status") or "").lower() not in _TERMINAL_STATUSES:
            control = dict(
                dict(raw.get("worker_execution_context") or {}).get(
                    "mail_task_control"
                )
                or {}
            )
            lease = dict(control.get("lease") or {})
            if int(lease.get("fencing_token") or 0) > 0:
                self._leases().release(
                    job_id=job_id,
                    fencing_token=int(lease["fencing_token"]),
                    owner_ref=None,
                    now=float(self._clock()),
                )
            self._update_status(
                job_id,
                "cancelled",
                status_reason_code="mail_task_cancelled_by_hub",
                event_type="mail_task_cancelled",
                event_actor=str(actor or "unknown"),
                event_details={
                    "account_ref_hash": _digest(envelope.get("account_ref")),
                    "idempotency_key_hash": _digest(envelope.get("idempotency_key")),
                },
            )
            self._audit_task("cancelled", envelope, actor)
        return self.get_task(job_id) or {}

    def cancel_account(
        self,
        *,
        account_ref: str,
        actor: str,
        operation: str | None = None,
    ) -> int:
        self._require_hub()
        account = _safe_reference(account_ref, field="account_ref")
        normalized_operation = (
            str(operation).strip().lower() if operation is not None else None
        )
        if (
            normalized_operation is not None
            and normalized_operation not in MAIL_OPERATIONS
        ):
            raise ValueError("mail_task_operation_invalid")
        rows = self._active_for_account(
            account,
            operation=normalized_operation,
        )
        for raw in rows:
            job_id = str(self._envelope(raw).get("job_id") or "")
            if job_id:
                self.cancel(job_id=job_id, actor=actor)
        return len(rows)

    def retry(self, *, job_id: str, actor: str) -> dict[str, Any]:
        self._require_hub()
        raw = self._raw(self._repository().get_by_id(job_id))
        envelope = self._envelope(raw)
        if not envelope:
            raise ValueError("mail_task_not_found")
        if str(raw.get("status") or "").lower() not in {"failed", "cancelled"}:
            raise RuntimeError("mail_task_retry_state_invalid")
        if float(envelope.get("deadline_at") or 0.0) <= float(self._clock()):
            raise RuntimeError("mail_task_deadline_exceeded")
        verification = dict(raw.get("verification_status") or {})
        retry_count = int(verification.get("mail_task_retry_count") or 0)
        if retry_count + 1 >= int(envelope.get("max_attempts") or 1):
            raise RuntimeError("mail_task_retry_budget_exhausted")
        result = verification.get("mail_task_result")
        if isinstance(result, Mapping) and not bool(result.get("retryable", False)):
            raise RuntimeError("mail_task_result_not_retryable")
        verification["mail_task_retry_count"] = retry_count + 1
        self._update_status(
            job_id,
            "todo",
            status_reason_code=None,
            error=None,
            verification_status=verification,
            event_type="mail_task_retried",
            event_actor=str(actor or "unknown"),
            event_details={"retry_count": retry_count + 1},
        )
        self._audit_task("retried", envelope, actor)
        return self.get_task(job_id) or {}

    def poll_accounts(
        self,
        *,
        account_refs: Sequence[str],
        workspace_scope: MailWorkspaceScope | Mapping[str, Any],
        sync_policy_ref: str,
        actor: str,
        interval_seconds: int = 300,
        max_tasks: int = 20,
    ) -> list[dict[str, Any]]:
        self._require_hub()
        interval = max(30, min(int(interval_seconds), 86400))
        limit = max(1, min(int(max_tasks), 100))
        slot = int(float(self._clock()) // interval)
        created: list[dict[str, Any]] = []
        for account_ref in sorted(set(account_refs))[:limit]:
            account_hash = _digest(account_ref)[:16]
            created.append(
                self.submit(
                    operation="sync",
                    account_ref=account_ref,
                    workspace_scope=workspace_scope,
                    idempotency_key=f"poll-{slot}-{account_hash}",
                    policy_refs={"sync_policy_ref": sync_policy_ref},
                    actor=actor,
                    priority="low",
                    deadline_seconds=min(interval, 900),
                )
            )
        return created

    def last_task_for_account(self, account_ref: str) -> dict[str, Any] | None:
        matches: list[tuple[float, dict[str, Any]]] = []
        for task in self._repository().get_all():
            raw = self._raw(task)
            envelope = self._envelope(raw)
            if str(envelope.get("account_ref") or "") != str(account_ref):
                continue
            view = self.get_task(str(envelope.get("job_id") or ""))
            if view is not None:
                matches.append((float(envelope.get("created_at") or 0.0), view))
        return max(matches, key=lambda item: item[0])[1] if matches else None

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = self._raw(self._repository().get_by_id(job_id))
        envelope = self._envelope(raw)
        if not envelope:
            raise ValueError("mail_task_not_found")
        payload = dict(result or {})
        if str(raw.get("status") or "").strip().lower() in _TERMINAL_STATUSES:
            raise ValueError("mail_task_result_terminal_state")
        if set(payload) != _RESULT_FIELDS:
            raise ValueError("mail_task_result_fields_invalid")
        if payload.get("schema") != MAIL_TASK_RESULT_SCHEMA:
            raise ValueError("mail_task_result_schema_invalid")
        if str(payload.get("job_id") or "") != job_id:
            raise ValueError("mail_task_result_job_mismatch")
        if payload.get("idempotency_key") != envelope.get("idempotency_key"):
            raise ValueError("mail_task_result_idempotency_mismatch")
        if payload.get("operation") != envelope.get("operation"):
            raise ValueError("mail_task_result_operation_mismatch")
        if str(payload.get("status") or "") not in {"completed", "failed"}:
            raise ValueError("mail_task_result_status_invalid")
        provider = str(payload.get("provider") or "")
        if provider and provider not in MAIL_PROVIDERS:
            raise ValueError("mail_task_result_provider_invalid")
        reason = str(payload.get("reason_code") or "")
        if reason and (
            not reason.startswith("mail_") or not reason.replace("_", "").isalnum()
        ):
            raise ValueError("mail_task_result_reason_invalid")
        refs = payload.get("result_refs")
        if not isinstance(refs, list) or any(
            _REFERENCE.fullmatch(str(item or "").strip()) is None for item in refs
        ):
            raise ValueError("mail_task_result_refs_invalid")
        counters = payload.get("counters")
        if not isinstance(counters, Mapping) or any(
            not str(key).replace("_", "").isalnum()
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for key, value in counters.items()
        ):
            raise ValueError("mail_task_result_counters_invalid")
        retry_after = payload.get("retry_after_ms")
        if retry_after is not None and (
            isinstance(retry_after, bool)
            or not isinstance(retry_after, int)
            or retry_after < 0
            or retry_after > 3600000
        ):
            raise ValueError("mail_task_result_retry_after_invalid")
        fencing = payload.get("lease_fencing_token")
        if isinstance(fencing, bool) or not isinstance(fencing, int) or fencing < 1:
            raise ValueError("mail_task_result_fencing_invalid")
        control = dict(
            dict(raw.get("worker_execution_context") or {}).get(
                "mail_task_control"
            )
            or {}
        )
        lease = dict(control.get("lease") or {})
        if (
            str(lease.get("job_id") or "") != job_id
            or int(lease.get("fencing_token") or 0) != fencing
            or float(lease.get("expires_at") or 0.0)
            <= float(self._clock())
        ):
            raise ValueError("mail_task_result_lease_stale")
        if _contains_forbidden_data(payload):
            raise ValueError("mail_task_result_sensitive_data_forbidden")
        return _clone(payload)

    def _audit_task(
        self,
        action: str,
        envelope: Mapping[str, Any],
        actor: str,
    ) -> None:
        self._audit(
            f"mail_task_{action}",
            {
                "job_id": envelope.get("job_id"),
                "operation": envelope.get("operation"),
                "account_ref_hash": _digest(envelope.get("account_ref")),
                "workspace_scope_hash": _digest(envelope.get("workspace_scope")),
                "idempotency_key_hash": _digest(envelope.get("idempotency_key")),
                "actor_hash": _digest(str(actor or "unknown")),
            },
        )


_MAIL_TASK_SERVICE: MailTaskService | None = None
_MAIL_TASK_SERVICE_LOCK = threading.Lock()


def get_mail_task_service() -> MailTaskService:
    global _MAIL_TASK_SERVICE
    if _MAIL_TASK_SERVICE is None:
        with _MAIL_TASK_SERVICE_LOCK:
            if _MAIL_TASK_SERVICE is None:
                _MAIL_TASK_SERVICE = MailTaskService()
    return _MAIL_TASK_SERVICE


__all__ = [
    "DatabaseMailAccountLeaseStore",
    "InMemoryMailAccountLeaseStore",
    "MAIL_OPERATIONS",
    "MAIL_TASK_KIND",
    "MAIL_TASK_RESULT_SCHEMA",
    "MAIL_TASK_SCHEMA",
    "MailAccountLease",
    "MailTaskService",
    "MailWorkspaceScope",
    "get_mail_task_service",
]
