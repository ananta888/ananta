"""SQL implementation of the Context Policy lifecycle repository ports."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import (
    ContextPolicyLifecycleAuditDB,
    ContextPolicyMutationDB,
    ContextPolicyVersionDB,
)
from agent.services.context_policy_lifecycle import (
    ContextPolicyLifecycleError,
    ContextPolicyVersion,
    derive_context_policy_etag,
)


class SQLContextPolicyLifecycleRepository:
    """Atomic version/CAS/idempotency store and append-only audit port."""

    def __init__(
        self,
        engine: Any,
        *,
        clock=lambda: datetime.now(timezone.utc).isoformat(),
    ) -> None:
        self._engine = engine
        self._clock = clock

    def latest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None:
        with Session(self._engine) as session:
            row = session.exec(
                self._scope_query(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_id=policy_id,
                ).order_by(
                    ContextPolicyVersionDB.version.desc()
                )
            ).first()
            return self._domain(row) if row is not None else None

    def get_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
    ) -> ContextPolicyVersion | None:
        with Session(self._engine) as session:
            row = session.exec(
                self._scope_query(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_id=policy_id,
                ).where(ContextPolicyVersionDB.version == version)
            ).first()
            return self._domain(row) if row is not None else None

    def list_versions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[Sequence[ContextPolicyVersion], str | None]:
        before = self._decode_cursor(cursor)
        statement = self._scope_query(
            tenant_id=tenant_id,
            project_id=project_id,
            policy_id=policy_id,
        )
        if before is not None:
            statement = statement.where(
                ContextPolicyVersionDB.version < before
            )
        statement = statement.order_by(
            ContextPolicyVersionDB.version.desc()
        ).limit(limit + 1)
        with Session(self._engine) as session:
            rows = list(session.exec(statement).all())
        page = rows[:limit]
        next_cursor = (
            f"v:{page[-1].version}"
            if len(rows) > limit and page
            else None
        )
        return tuple(self._domain(row) for row in page), next_cursor

    def active(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None:
        with Session(self._engine) as session:
            rows = list(
                session.exec(
                    self._scope_query(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        policy_id=policy_id,
                    )
                    .where(ContextPolicyVersionDB.state == "active")
                    .limit(2)
                ).all()
            )
        if len(rows) > 1:
            raise ContextPolicyLifecycleError(
                "multiple_active_policy_snapshots"
            )
        return self._domain(rows[0]) if rows else None

    def get_mutation_result(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> ContextPolicyVersion | None:
        with Session(self._engine) as session:
            mutation = self._find_mutation(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                policy_id=policy_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            return self._mutation_result(
                session,
                mutation=mutation,
                request_digest=request_digest,
            )

    def append_draft(
        self,
        *,
        version: ContextPolicyVersion,
        expected_latest_version: int | None,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion:
        try:
            with Session(self._engine) as session:
                replay = self._replay_in_session(
                    session,
                    version=version,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return replay
                latest = session.exec(
                    self._scope_query(
                        tenant_id=version.tenant_id,
                        project_id=version.project_id,
                        policy_id=version.policy_id,
                    ).order_by(
                        ContextPolicyVersionDB.version.desc()
                    )
                ).first()
                actual_latest = (
                    latest.version if latest is not None else None
                )
                if actual_latest != expected_latest_version:
                    raise ContextPolicyLifecycleError(
                        "policy_version_conflict"
                    )
                if version.version != (actual_latest or 0) + 1:
                    raise ContextPolicyLifecycleError(
                        "policy_version_conflict"
                    )
                session.add(self._row(version))
                self._add_mutation(
                    session,
                    result=version,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                session.commit()
                return version
        except IntegrityError as exc:
            replay = self._replay_after_conflict(
                version=version,
                operation=operation,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            raise ContextPolicyLifecycleError(
                "policy_version_conflict"
            ) from exc

    def transition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        expected_etag: str,
        target_state: str,
        actor_id: str,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion:
        if target_state not in {"active", "revoked"}:
            raise ContextPolicyLifecycleError(
                "policy_transition_invalid"
            )
        try:
            with Session(self._engine) as session:
                mutation = self._find_mutation(
                    session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_id=policy_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                )
                replay = self._mutation_result(
                    session,
                    mutation=mutation,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return replay
                target = session.exec(
                    self._scope_query(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        policy_id=policy_id,
                    )
                    .where(ContextPolicyVersionDB.version == version)
                    .with_for_update()
                ).first()
                expected_state = (
                    "draft" if target_state == "active" else "active"
                )
                if (
                    target is None
                    or target.etag != expected_etag
                    or target.state != expected_state
                ):
                    raise ContextPolicyLifecycleError(
                        "policy_version_conflict"
                    )
                now = self._now()
                if target_state == "active":
                    self._supersede_active(
                        session,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        policy_id=policy_id,
                        actor_id=actor_id,
                        now=now,
                    )
                result_etag = derive_context_policy_etag(
                    policy_id=policy_id,
                    version=version,
                    policy_digest=target.policy_digest,
                    state=target_state,
                )
                update_result = session.exec(
                    sa.update(ContextPolicyVersionDB)
                    .where(
                        ContextPolicyVersionDB.record_id
                        == target.record_id,
                        ContextPolicyVersionDB.etag == expected_etag,
                        ContextPolicyVersionDB.state == expected_state,
                    )
                    .values(
                        state=target_state,
                        etag=result_etag,
                        updated_by=actor_id,
                        updated_at=now,
                    )
                )
                if update_result.rowcount != 1:
                    raise ContextPolicyLifecycleError(
                        "policy_version_conflict"
                    )
                result = ContextPolicyVersion(
                    policy_id=policy_id,
                    version=version,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    state=target_state,
                    document=dict(target.document_json),
                    policy_digest=target.policy_digest,
                    etag=result_etag,
                    created_by=target.created_by,
                    created_at=target.created_at,
                )
                self._add_mutation(
                    session,
                    result=result,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                session.commit()
                return result
        except IntegrityError as exc:
            replay = self._replay_after_scope_conflict(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_id=policy_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            raise ContextPolicyLifecycleError(
                "policy_version_conflict"
            ) from exc

    def record(
        self,
        *,
        operation: str,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        policy_digest: str,
        reason_code: str,
    ) -> None:
        created_at = self._now()
        audit_id = _digest(
            {
                "operation": operation,
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "policy_id": policy_id,
                "version": version,
                "policy_digest": policy_digest,
                "reason_code": reason_code,
            }
        )
        try:
            with Session(self._engine) as session:
                session.add(
                    ContextPolicyLifecycleAuditDB(
                        audit_id=audit_id,
                        operation=operation,
                        actor_id=actor_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        policy_id=policy_id,
                        version=version,
                        policy_digest=policy_digest,
                        reason_code=reason_code,
                        created_at=created_at,
                    )
                )
                session.commit()
        except IntegrityError:
            return

    def _supersede_active(
        self,
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        actor_id: str,
        now: str,
    ) -> None:
        active = session.exec(
            self._scope_query(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_id=policy_id,
            )
            .where(ContextPolicyVersionDB.state == "active")
            .with_for_update()
        ).first()
        if active is None:
            return
        superseded_etag = derive_context_policy_etag(
            policy_id=active.policy_id,
            version=active.version,
            policy_digest=active.policy_digest,
            state="superseded",
        )
        result = session.exec(
            sa.update(ContextPolicyVersionDB)
            .where(
                ContextPolicyVersionDB.record_id == active.record_id,
                ContextPolicyVersionDB.etag == active.etag,
                ContextPolicyVersionDB.state == "active",
            )
            .values(
                state="superseded",
                etag=superseded_etag,
                updated_by=actor_id,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ContextPolicyLifecycleError(
                "policy_version_conflict"
            )

    def _replay_in_session(
        self,
        session: Session,
        *,
        version: ContextPolicyVersion,
        operation: str | None,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> ContextPolicyVersion | None:
        mutation = self._find_mutation(
            session,
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            policy_id=version.policy_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
        return self._mutation_result(
            session,
            mutation=mutation,
            request_digest=request_digest,
        )

    def _replay_after_conflict(
        self,
        *,
        version: ContextPolicyVersion,
        operation: str | None,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> ContextPolicyVersion | None:
        return self._replay_after_scope_conflict(
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            policy_id=version.policy_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def _replay_after_scope_conflict(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        operation: str | None,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> ContextPolicyVersion | None:
        if not operation or not idempotency_key or not request_digest:
            return None
        return self.get_mutation_result(
            tenant_id=tenant_id,
            project_id=project_id,
            policy_id=policy_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    @staticmethod
    def _find_mutation(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        operation: str | None,
        idempotency_key: str | None,
    ) -> ContextPolicyMutationDB | None:
        if not operation or not idempotency_key:
            return None
        return session.exec(
            select(ContextPolicyMutationDB).where(
                ContextPolicyMutationDB.tenant_id == tenant_id,
                ContextPolicyMutationDB.project_id == project_id,
                ContextPolicyMutationDB.policy_id == policy_id,
                ContextPolicyMutationDB.operation == operation,
                ContextPolicyMutationDB.idempotency_key
                == _idempotency_key_digest(idempotency_key),
            )
        ).first()

    @staticmethod
    def _mutation_result(
        session: Session,
        *,
        mutation: ContextPolicyMutationDB | None,
        request_digest: str | None,
    ) -> ContextPolicyVersion | None:
        if mutation is None:
            return None
        if (
            not request_digest
            or mutation.request_digest != request_digest
        ):
            raise ContextPolicyLifecycleError(
                "policy_idempotency_conflict"
            )
        payload = dict(mutation.result_json or {})
        if (
            payload.get("version") != mutation.result_version
            or payload.get("etag") != mutation.result_etag
        ):
            raise ContextPolicyLifecycleError(
                "policy_idempotency_result_missing"
            )
        try:
            return ContextPolicyVersion(**payload)
        except (TypeError, ValueError) as exc:
            raise ContextPolicyLifecycleError(
                "policy_idempotency_result_invalid"
            ) from exc

    def _add_mutation(
        self,
        session: Session,
        *,
        result: ContextPolicyVersion,
        operation: str | None,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> None:
        if not operation and not idempotency_key and not request_digest:
            return
        if not operation or not idempotency_key or not request_digest:
            raise ContextPolicyLifecycleError(
                "policy_idempotency_metadata_incomplete"
            )
        session.add(
            ContextPolicyMutationDB(
                mutation_id=_digest(
                    {
                        "tenant_id": result.tenant_id,
                        "project_id": result.project_id,
                        "policy_id": result.policy_id,
                        "operation": operation,
                        "idempotency_key": idempotency_key,
                    }
                ),
                tenant_id=result.tenant_id,
                project_id=result.project_id,
                policy_id=result.policy_id,
                operation=operation,
                idempotency_key=_idempotency_key_digest(
                    idempotency_key
                ),
                request_digest=request_digest,
                result_version=result.version,
                result_etag=result.etag,
                result_json={
                    "policy_id": result.policy_id,
                    "version": result.version,
                    "tenant_id": result.tenant_id,
                    "project_id": result.project_id,
                    "state": result.state,
                    "document": dict(result.document),
                    "policy_digest": result.policy_digest,
                    "etag": result.etag,
                    "created_by": result.created_by,
                    "created_at": result.created_at,
                },
                created_at=self._now(),
            )
        )

    @staticmethod
    def _row(version: ContextPolicyVersion) -> ContextPolicyVersionDB:
        return ContextPolicyVersionDB(
            record_id=_digest(
                {
                    "tenant_id": version.tenant_id,
                    "project_id": version.project_id,
                    "policy_id": version.policy_id,
                    "version": version.version,
                }
            ),
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            policy_id=version.policy_id,
            version=version.version,
            state=version.state,
            document_json=dict(version.document),
            policy_digest=version.policy_digest,
            etag=version.etag,
            created_by=version.created_by,
            created_at=version.created_at,
            updated_by=version.created_by,
            updated_at=version.created_at,
        )

    @staticmethod
    def _domain(row: ContextPolicyVersionDB) -> ContextPolicyVersion:
        return ContextPolicyVersion(
            policy_id=row.policy_id,
            version=row.version,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            state=row.state,
            document=dict(row.document_json),
            policy_digest=row.policy_digest,
            etag=row.etag,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _scope_query(
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ):
        return select(ContextPolicyVersionDB).where(
            ContextPolicyVersionDB.tenant_id == tenant_id,
            ContextPolicyVersionDB.project_id == project_id,
            ContextPolicyVersionDB.policy_id == policy_id,
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int | None:
        if cursor is None:
            return None
        raw = str(cursor)
        if not raw.startswith("v:"):
            raise ContextPolicyLifecycleError(
                "policy_cursor_invalid"
            )
        try:
            value = int(raw[2:])
        except ValueError as exc:
            raise ContextPolicyLifecycleError(
                "policy_cursor_invalid"
            ) from exc
        if value < 1:
            raise ContextPolicyLifecycleError(
                "policy_cursor_invalid"
            )
        return value

    def _now(self) -> str:
        value = str(self._clock() or "")
        if not value:
            raise ContextPolicyLifecycleError(
                "policy_repository_clock_invalid"
            )
        return value


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _idempotency_key_digest(value: str) -> str:
    return hashlib.sha256(
        b"ananta.context-policy.idempotency.v1\0"
        + str(value).encode("utf-8")
    ).hexdigest()


__all__ = ["SQLContextPolicyLifecycleRepository"]
