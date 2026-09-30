"""Per-account mail leases with monotonically increasing fencing tokens.

The mail task service owns the lifecycle and chooses the lease store; this
module only provides the lease value, its store port and the in-memory and
TaskDB-backed implementations (SRP, substitutable behind the port - LSP/DIP).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.mail_task_contract import MAIL_TASK_KIND
from agent.services.mail_task_contract import MAIL_TASK_TERMINAL_STATUSES as _TERMINAL_STATUSES


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


__all__ = [
    "DatabaseMailAccountLeaseStore",
    "InMemoryMailAccountLeaseStore",
    "MailAccountLease",
    "MailAccountLeaseStorePort",
]
