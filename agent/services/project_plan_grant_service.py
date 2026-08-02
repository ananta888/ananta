"""Project-scoped, plan-bound one-shot grants for pre-creation mutations."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import OrganizationAdminGrantDB

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_GRANT_KINDS = frozenset({"instantiate", "definition_mutation", "definition_reconcile", "bundle_import"})


class ProjectPlanGrantError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ProjectPlanGrantService:
    """Issue and atomically consume principal-bound project mutation grants."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._clock = clock

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def issue(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        plan_digest: str,
        policy_hash: str,
        grant_kind: str,
        granted_by: str,
        idempotency_key: str,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        values = {
            "tenant_id": self._identity(tenant_id, "tenant_id"),
            "project_id": self._identity(project_id, "project_id"),
            "principal_id": self._identity(principal_id, "principal_id"),
            "plan_digest": self._digest(plan_digest, "plan_digest"),
            "policy_hash": self._digest(policy_hash, "policy_hash"),
            "grant_kind": self._grant_kind(grant_kind),
            "granted_by": self._identity(granted_by, "granted_by"),
            "idempotency_key": self._idempotency_key(idempotency_key),
        }
        ttl = max(60, min(int(ttl_seconds), 3600))
        now = self._clock()
        grant_id = self._stable_id(
            values["tenant_id"],
            values["project_id"],
            values["principal_id"],
            values["grant_kind"],
            values["idempotency_key"],
        )
        with self._session_factory() as session:
            existing = self._by_id_or_binding(
                session,
                grant_id=grant_id,
                tenant_id=values["tenant_id"],
                project_id=values["project_id"],
                principal_id=values["principal_id"],
                plan_digest=values["plan_digest"],
                grant_kind=values["grant_kind"],
                idempotency_key=values["idempotency_key"],
            )
            if existing is not None:
                return self._replay(
                    existing,
                    expected_policy_hash=values["policy_hash"],
                    now=now,
                )
            grant = OrganizationAdminGrantDB(
                grant_id=grant_id,
                tenant_id=values["tenant_id"],
                project_id=values["project_id"],
                organization_id=None,
                plan_digest=values["plan_digest"],
                principal_id=values["principal_id"],
                grant_kind=values["grant_kind"],
                idempotency_key=values["idempotency_key"],
                policy_hash=values["policy_hash"],
                granted_by=values["granted_by"],
                expires_at=now + ttl,
            )
            try:
                with session.begin_nested():
                    session.add(grant)
                    session.flush()
            except IntegrityError:
                existing = self._by_id_or_binding(
                    session,
                    grant_id=grant_id,
                    tenant_id=values["tenant_id"],
                    project_id=values["project_id"],
                    principal_id=values["principal_id"],
                    plan_digest=values["plan_digest"],
                    grant_kind=values["grant_kind"],
                    idempotency_key=values["idempotency_key"],
                )
                if existing is None:
                    raise ProjectPlanGrantError("project_plan_grant_write_conflict")
                return self._replay(
                    existing,
                    expected_policy_hash=values["policy_hash"],
                    now=now,
                )
            session.commit()
            session.refresh(grant)
            return self._response(grant, replayed=False)

    def consume_in_session(
        self,
        session: Session,
        *,
        grant_id: str,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        plan_digest: str,
        policy_hash: str,
        grant_kind: str,
    ) -> OrganizationAdminGrantDB:
        """Consume exactly once inside the caller's aggregate transaction."""

        now = self._clock()
        normalized_kind = self._grant_kind(grant_kind)
        result = session.exec(
            update(OrganizationAdminGrantDB)
            .where(
                OrganizationAdminGrantDB.grant_id == str(grant_id or ""),
                OrganizationAdminGrantDB.tenant_id == str(tenant_id or ""),
                OrganizationAdminGrantDB.project_id == str(project_id or ""),
                OrganizationAdminGrantDB.organization_id.is_(None),
                OrganizationAdminGrantDB.principal_id == str(principal_id or ""),
                OrganizationAdminGrantDB.plan_digest == str(plan_digest or ""),
                OrganizationAdminGrantDB.policy_hash == str(policy_hash or ""),
                OrganizationAdminGrantDB.grant_kind == normalized_kind,
                OrganizationAdminGrantDB.revoked_at.is_(None),
                OrganizationAdminGrantDB.expires_at > now,
            )
            .values(revoked_at=now)
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            raise ProjectPlanGrantError("project_plan_grant_invalid")
        row = session.get(OrganizationAdminGrantDB, str(grant_id or ""))
        if row is None:
            raise ProjectPlanGrantError("project_plan_grant_invalid")
        return row

    @staticmethod
    def _by_id_or_binding(
        session: Session,
        *,
        grant_id: str,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        plan_digest: str,
        grant_kind: str,
        idempotency_key: str,
    ) -> OrganizationAdminGrantDB | None:
        by_id = session.get(OrganizationAdminGrantDB, grant_id)
        if by_id is not None:
            if any(
                str(actual or "") != expected
                for actual, expected in (
                    (by_id.tenant_id, tenant_id),
                    (by_id.project_id, project_id),
                    (by_id.principal_id, principal_id),
                    (by_id.plan_digest, plan_digest),
                    (by_id.grant_kind, grant_kind),
                    (by_id.idempotency_key, idempotency_key),
                )
            ):
                raise ProjectPlanGrantError("project_plan_grant_idempotency_conflict")
            return by_id
        return session.exec(
            select(OrganizationAdminGrantDB).where(
                OrganizationAdminGrantDB.tenant_id == tenant_id,
                OrganizationAdminGrantDB.project_id == project_id,
                OrganizationAdminGrantDB.organization_id.is_(None),
                OrganizationAdminGrantDB.principal_id == principal_id,
                OrganizationAdminGrantDB.plan_digest == plan_digest,
                OrganizationAdminGrantDB.grant_kind == grant_kind,
                OrganizationAdminGrantDB.idempotency_key == idempotency_key,
            )
        ).one_or_none()

    def _replay(
        self,
        row: OrganizationAdminGrantDB,
        *,
        expected_policy_hash: str,
        now: float,
    ) -> dict[str, Any]:
        if row.policy_hash != expected_policy_hash:
            raise ProjectPlanGrantError("project_plan_grant_idempotency_conflict")
        if row.revoked_at is not None:
            raise ProjectPlanGrantError("project_plan_grant_already_consumed")
        if row.expires_at is None or float(row.expires_at) <= now:
            raise ProjectPlanGrantError("project_plan_grant_expired")
        return self._response(row, replayed=True)

    @staticmethod
    def _response(
        row: OrganizationAdminGrantDB,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "grant_id": row.grant_id,
            "grant_kind": row.grant_kind,
            "tenant_id": row.tenant_id,
            "project_id": row.project_id,
            "principal_id": row.principal_id,
            "plan_digest": row.plan_digest,
            "policy_hash": row.policy_hash,
            "expires_at": row.expires_at,
            "replayed": replayed,
        }

    @staticmethod
    def _identity(value: str, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 191 or any(character.isspace() for character in normalized):
            raise ProjectPlanGrantError(f"project_plan_grant_{field}_invalid")
        return normalized

    @staticmethod
    def _digest(value: str, field: str) -> str:
        normalized = str(value or "").strip().lower()
        if _SHA256.fullmatch(normalized) is None:
            raise ProjectPlanGrantError(f"project_plan_grant_{field}_invalid")
        return normalized

    @staticmethod
    def _grant_kind(value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in _GRANT_KINDS:
            raise ProjectPlanGrantError("project_plan_grant_kind_invalid")
        return normalized

    @staticmethod
    def _idempotency_key(value: str) -> str:
        normalized = str(value or "").strip()
        if not 8 <= len(normalized) <= 191 or any(character.isspace() for character in normalized):
            raise ProjectPlanGrantError("project_plan_grant_idempotency_key_invalid")
        return normalized

    @staticmethod
    def _stable_id(*values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:32]
        return f"opgrant-{digest}"


__all__ = ["ProjectPlanGrantError", "ProjectPlanGrantService"]
