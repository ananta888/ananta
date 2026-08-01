"""Atomic SQL persistence and content-free audit for public remotes."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control_public_remote import (
    SourceControlPublicRemoteAuditDB,
    SourceControlPublicRemoteDB,
    SourceControlPublicRemoteValidationDB,
)
from agent.services.source_control_public_remote_contracts import (
    PublicRemoteRecord,
    PublicRemoteSelection,
    PublicRemoteValidationBinding,
)
from agent.sources.git_source_connector_common import GitSourceScope

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_EVENT = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")


class SourceControlPublicRemotePersistenceError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class SQLSourceControlPublicRemoteRepository:
    """Own TTL handles, durable records, and their audit in Hub SQL."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def store_validation(
        self,
        *,
        handle_digest: str,
        binding: PublicRemoteValidationBinding,
        expires_at_epoch: float,
    ) -> None:
        now = float(self._clock())
        if (
            _DIGEST.fullmatch(handle_digest) is None
            or expires_at_epoch <= now
        ):
            raise SourceControlPublicRemotePersistenceError(
                "public_remote_validation_invalid"
            )
        selection = binding.selection
        row = SourceControlPublicRemoteValidationDB(
            handle_digest=handle_digest,
            tenant_id=binding.scope.tenant_id,
            project_id=binding.scope.project_id,
            owner_id=binding.scope.owner_id,
            provider=selection.provider,
            host=selection.host,
            repository_path=selection.repository_path,
            requested_ref=selection.requested_ref,
            commit_sha=binding.commit_sha,
            policy_digest=binding.policy_digest,
            binding_digest=binding.binding_digest,
            expires_at_epoch=float(expires_at_epoch),
            consumed_at_epoch=None,
            remote_id=None,
            created_at_epoch=now,
        )
        with self._session_factory() as session:
            session.add(row)
            session.add(
                self._audit_row(
                    scope=binding.scope,
                    event_type="validate",
                    decision="allow",
                    reason_code="public_remote_validated",
                    binding_digest=binding.binding_digest,
                    now=now,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SourceControlPublicRemotePersistenceError(
                    "public_remote_validation_handle_collision",
                    status_code=409,
                ) from exc

    def consume_validation(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
        remote_id: str,
    ) -> PublicRemoteRecord:
        now = float(self._clock())
        with self._session_factory() as session:
            validation = session.get(
                SourceControlPublicRemoteValidationDB,
                handle_digest,
            )
            if validation is None or not self._scope_matches(validation, scope):
                raise SourceControlPublicRemotePersistenceError(
                    "public_remote_validation_not_found",
                    status_code=404,
                )
            binding = self._validation_binding(validation)
            if validation.consumed_at_epoch is not None:
                return self._require_remote_for_handle(
                    session,
                    handle_digest=handle_digest,
                    scope=scope,
                )
            if now >= float(validation.expires_at_epoch):
                raise SourceControlPublicRemotePersistenceError(
                    "public_remote_validation_expired",
                    status_code=409,
                )
            remote = SourceControlPublicRemoteDB(
                remote_id=remote_id,
                handle_digest=handle_digest,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=scope.owner_id,
                provider=binding.selection.provider,
                host=binding.selection.host,
                repository_path=binding.selection.repository_path,
                requested_ref=binding.selection.requested_ref,
                validated_commit_sha=binding.commit_sha,
                policy_digest=binding.policy_digest,
                binding_digest=binding.binding_digest,
                created_at_epoch=now,
            )
            validation.consumed_at_epoch = now
            validation.remote_id = remote_id
            session.add(validation)
            session.add(remote)
            session.add(
                self._audit_row(
                    scope=scope,
                    event_type="create",
                    decision="allow",
                    reason_code="public_remote_created",
                    binding_digest=binding.binding_digest,
                    now=now,
                )
            )
            try:
                session.commit()
                session.refresh(remote)
                return self._remote_record(remote)
            except IntegrityError as exc:
                session.rollback()
                existing = self._remote_for_handle(
                    session,
                    handle_digest=handle_digest,
                    scope=scope,
                )
                if existing is not None:
                    return existing
                raise SourceControlPublicRemotePersistenceError(
                    "public_remote_id_collision",
                    status_code=409,
                ) from exc

    def get_consumed_validation(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> PublicRemoteRecord:
        with self._session_factory() as session:
            return self._require_remote_for_handle(
                session,
                handle_digest=handle_digest,
                scope=scope,
            )

    def record_denial(
        self,
        *,
        scope: GitSourceScope,
        event_type: str,
        reason_code: str,
        binding_digest: str,
    ) -> None:
        with self._session_factory() as session:
            session.add(
                self._audit_row(
                    scope=scope,
                    event_type=event_type,
                    decision="deny",
                    reason_code=reason_code,
                    binding_digest=binding_digest,
                    now=float(self._clock()),
                )
            )
            session.commit()

    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[object, ...]:
        with self._session_factory() as session:
            statement = select(SourceControlPublicRemoteDB).where(
                SourceControlPublicRemoteDB.tenant_id == tenant_id,
                SourceControlPublicRemoteDB.project_id == project_id,
            )
            if owner_id is not None:
                statement = statement.where(
                    SourceControlPublicRemoteDB.owner_id == owner_id
                )
            rows = session.exec(
                statement.order_by(SourceControlPublicRemoteDB.remote_id)
            ).all()
            return tuple(
                self._remote_record(row).registered_authorization()
                for row in rows
            )

    def resolve_registered_remote(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        remote_id: str,
    ):
        with self._session_factory() as session:
            statement = select(SourceControlPublicRemoteDB).where(
                SourceControlPublicRemoteDB.remote_id == remote_id,
                SourceControlPublicRemoteDB.tenant_id == tenant_id,
                SourceControlPublicRemoteDB.project_id == project_id,
            )
            if owner_id is not None:
                statement = statement.where(
                    SourceControlPublicRemoteDB.owner_id == owner_id
                )
            rows = session.exec(statement).all()
            if len(rows) != 1:
                return None
            return self._remote_record(rows[0]).registered_authorization()

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ):
        record = self.resolve_registered_remote(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            owner_id=scope.owner_id,
            remote_id=connection_ref,
        )
        if record is None:
            return None
        if record.repository != repository_identifier:
            return None
        return record

    def resolve_github(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str,
    ):
        record = self.resolve_connection(
            scope=scope,
            connection_ref=authorization_ref,
            repository_identifier=repository,
        )
        if (
            record is None
            or record.authorization_kind != "github_public"
        ):
            return None
        return record

    def resolve_generic(
        self,
        *,
        scope: GitSourceScope,
        remote_id: str,
    ):
        record = self.resolve_connection(
            scope=scope,
            connection_ref=remote_id,
            repository_identifier=None,
        )
        if (
            record is None
            or record.authorization_kind != "generic_git"
        ):
            return None
        return record

    @staticmethod
    def _scope_matches(row: object, scope: GitSourceScope) -> bool:
        return (
            getattr(row, "tenant_id", None) == scope.tenant_id
            and getattr(row, "project_id", None) == scope.project_id
            and getattr(row, "owner_id", None) == scope.owner_id
        )

    def _remote_for_handle(
        self,
        session: Session,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> PublicRemoteRecord | None:
        rows = session.exec(
            select(SourceControlPublicRemoteDB).where(
                SourceControlPublicRemoteDB.handle_digest == handle_digest,
                SourceControlPublicRemoteDB.tenant_id == scope.tenant_id,
                SourceControlPublicRemoteDB.project_id == scope.project_id,
                SourceControlPublicRemoteDB.owner_id == scope.owner_id,
            )
        ).all()
        return self._remote_record(rows[0]) if len(rows) == 1 else None

    def _require_remote_for_handle(
        self,
        session: Session,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> PublicRemoteRecord:
        record = self._remote_for_handle(
            session,
            handle_digest=handle_digest,
            scope=scope,
        )
        if record is None:
            raise SourceControlPublicRemotePersistenceError(
                "public_remote_consumption_inconsistent",
                status_code=409,
            )
        return record

    @staticmethod
    def _validation_binding(
        row: SourceControlPublicRemoteValidationDB,
    ) -> PublicRemoteValidationBinding:
        binding = PublicRemoteValidationBinding(
            scope=GitSourceScope(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
            ),
            selection=PublicRemoteSelection(
                provider=row.provider,
                host=row.host,
                repository_path=row.repository_path,
                requested_ref=row.requested_ref,
            ),
            commit_sha=row.commit_sha,
            policy_digest=row.policy_digest,
        )
        if binding.binding_digest != row.binding_digest:
            raise SourceControlPublicRemotePersistenceError(
                "public_remote_validation_binding_invalid",
                status_code=409,
            )
        return binding

    @staticmethod
    def _remote_record(
        row: SourceControlPublicRemoteDB,
    ) -> PublicRemoteRecord:
        binding = PublicRemoteValidationBinding(
            scope=GitSourceScope(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
            ),
            selection=PublicRemoteSelection(
                provider=row.provider,
                host=row.host,
                repository_path=row.repository_path,
                requested_ref=row.requested_ref,
            ),
            commit_sha=row.validated_commit_sha,
            policy_digest=row.policy_digest,
        )
        if binding.binding_digest != row.binding_digest:
            raise SourceControlPublicRemotePersistenceError(
                "public_remote_binding_invalid",
                status_code=409,
            )
        return PublicRemoteRecord(
            remote_id=row.remote_id,
            binding=binding,
            created_at_epoch=row.created_at_epoch,
        )

    @staticmethod
    def _audit_row(
        *,
        scope: GitSourceScope,
        event_type: str,
        decision: str,
        reason_code: str,
        binding_digest: str,
        now: float,
    ) -> SourceControlPublicRemoteAuditDB:
        if (
            _EVENT.fullmatch(event_type) is None
            or decision not in {"allow", "deny"}
            or _REASON.fullmatch(reason_code) is None
            or _DIGEST.fullmatch(binding_digest) is None
        ):
            raise SourceControlPublicRemotePersistenceError(
                "public_remote_audit_invalid"
            )
        return SourceControlPublicRemoteAuditDB(
            audit_id=uuid.uuid4().hex,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            actor_id=scope.owner_id,
            event_type=event_type,
            decision=decision,
            reason_code=reason_code,
            binding_digest=binding_digest,
            occurred_at_epoch=now,
        )


__all__ = [
    "SQLSourceControlPublicRemoteRepository",
    "SourceControlPublicRemotePersistenceError",
]
