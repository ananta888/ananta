"""Atomic workspace validation, registration, CAS, and audit persistence."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control_workspace_registration import (
    SourceControlWorkspaceRegistrationAuditDB,
    SourceControlWorkspaceRegistrationDB,
    SourceControlWorkspaceValidationDB,
)
from agent.services.source_control_workspace_contracts import (
    WorkspaceFolderSnapshot,
    WorkspaceRegistrationRecord,
    WorkspaceValidationBinding,
)
from agent.sources.git_source_connector_common import GitSourceScope

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class SourceControlWorkspacePersistenceError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class SQLSourceControlWorkspaceRegistrationRepository:
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
        binding: WorkspaceValidationBinding,
        expires_at_epoch: float,
    ) -> None:
        now = float(self._clock())
        if (
            _DIGEST.fullmatch(handle_digest) is None
            or expires_at_epoch <= now
        ):
            raise SourceControlWorkspacePersistenceError(
                "workspace_validation_invalid"
            )
        row = SourceControlWorkspaceValidationDB(
            handle_digest=handle_digest,
            tenant_id=binding.scope.tenant_id,
            project_id=binding.scope.project_id,
            owner_id=binding.scope.owner_id,
            folder_handle=binding.folder_handle,
            root_fingerprint=binding.root_fingerprint,
            manifest_digest=binding.manifest_digest,
            expires_at_epoch=expires_at_epoch,
            consumed_at_epoch=None,
            workspace_id=None,
            created_at_epoch=now,
        )
        with self._session_factory() as session:
            session.add(row)
            session.add(
                self._audit(
                    scope=binding.scope,
                    workspace_id="validation",
                    event_type="validate",
                    decision="allow",
                    reason_code="workspace_folder_validated",
                    now=now,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_handle_collision",
                    status_code=409,
                ) from exc

    def validation_binding(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> WorkspaceValidationBinding:
        with self._session_factory() as session:
            row = session.get(SourceControlWorkspaceValidationDB, handle_digest)
            if row is None or not self._scope_matches(row, scope):
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_not_found",
                    status_code=404,
                )
            if row.consumed_at_epoch is None and float(
                self._clock()
            ) >= float(row.expires_at_epoch):
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_expired",
                    status_code=409,
                )
            return self._validation_record(row)

    def consume_validation(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
        snapshot: WorkspaceFolderSnapshot,
        workspace_id: str,
    ) -> WorkspaceRegistrationRecord:
        now = float(self._clock())
        with self._session_factory() as session:
            validation = session.get(
                SourceControlWorkspaceValidationDB,
                handle_digest,
            )
            if validation is None or not self._scope_matches(
                validation,
                scope,
            ):
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_not_found",
                    status_code=404,
                )
            if validation.consumed_at_epoch is not None:
                return self._require_by_validation(
                    session,
                    handle_digest=handle_digest,
                    scope=scope,
                )
            if now >= float(validation.expires_at_epoch):
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_expired",
                    status_code=409,
                )
            if (
                snapshot.folder_handle != validation.folder_handle
                or snapshot.root_fingerprint
                != validation.root_fingerprint
                or snapshot.manifest_digest != validation.manifest_digest
            ):
                raise SourceControlWorkspacePersistenceError(
                    "workspace_validation_revalidation_failed",
                    status_code=409,
                )
            existing = session.exec(
                select(SourceControlWorkspaceRegistrationDB).where(
                    SourceControlWorkspaceRegistrationDB.tenant_id
                    == scope.tenant_id,
                    SourceControlWorkspaceRegistrationDB.project_id
                    == scope.project_id,
                    SourceControlWorkspaceRegistrationDB.owner_id
                    == scope.owner_id,
                    SourceControlWorkspaceRegistrationDB.folder_handle
                    == validation.folder_handle,
                )
            ).first()
            if existing is not None:
                if existing.registration_state != "active":
                    raise SourceControlWorkspacePersistenceError(
                        "workspace_registration_disabled",
                        status_code=409,
                    )
                validation.consumed_at_epoch = now
                validation.workspace_id = existing.workspace_id
                session.add(validation)
                session.add(
                    self._audit(
                        scope=scope,
                        workspace_id=existing.workspace_id,
                        event_type="create",
                        decision="allow",
                        reason_code="workspace_registration_reused",
                        now=now,
                    )
                )
                session.commit()
                return self._registration_record(existing)
            row = SourceControlWorkspaceRegistrationDB(
                workspace_id=workspace_id,
                validation_handle_digest=handle_digest,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=scope.owner_id,
                folder_handle=validation.folder_handle,
                root_fingerprint=validation.root_fingerprint,
                manifest_digest=validation.manifest_digest,
                registration_state="active",
                read_only=True,
                lock_version=1,
                created_at_epoch=now,
                updated_at_epoch=now,
            )
            validation.consumed_at_epoch = now
            validation.workspace_id = workspace_id
            session.add(validation)
            session.add(row)
            session.add(
                self._audit(
                    scope=scope,
                    workspace_id=workspace_id,
                    event_type="create",
                    decision="allow",
                    reason_code="workspace_registered",
                    now=now,
                )
            )
            try:
                session.commit()
                session.refresh(row)
                return self._registration_record(row)
            except IntegrityError as exc:
                session.rollback()
                recovered = self._by_validation(
                    session,
                    handle_digest=handle_digest,
                    scope=scope,
                )
                if recovered is not None:
                    return recovered
                raise SourceControlWorkspacePersistenceError(
                    "workspace_registration_conflict",
                    status_code=409,
                ) from exc

    def get_by_validation(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> WorkspaceRegistrationRecord:
        with self._session_factory() as session:
            return self._require_by_validation(
                session,
                handle_digest=handle_digest,
                scope=scope,
            )

    def get_registration(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> WorkspaceRegistrationRecord | None:
        with self._session_factory() as session:
            statement = select(
                SourceControlWorkspaceRegistrationDB
            ).where(
                SourceControlWorkspaceRegistrationDB.workspace_id
                == workspace_id,
                SourceControlWorkspaceRegistrationDB.tenant_id
                == tenant_id,
                SourceControlWorkspaceRegistrationDB.project_id
                == project_id,
            )
            if owner_id is not None:
                statement = statement.where(
                    SourceControlWorkspaceRegistrationDB.owner_id
                    == owner_id
                )
            rows = session.exec(statement).all()
            return (
                self._registration_record(rows[0])
                if len(rows) == 1
                else None
            )

    def list_registrations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[WorkspaceRegistrationRecord, ...]:
        with self._session_factory() as session:
            statement = select(
                SourceControlWorkspaceRegistrationDB
            ).where(
                SourceControlWorkspaceRegistrationDB.tenant_id
                == tenant_id,
                SourceControlWorkspaceRegistrationDB.project_id
                == project_id,
            )
            if owner_id is not None:
                statement = statement.where(
                    SourceControlWorkspaceRegistrationDB.owner_id
                    == owner_id
                )
            rows = session.exec(
                statement.order_by(
                    SourceControlWorkspaceRegistrationDB.workspace_id
                )
            ).all()
            return tuple(self._registration_record(row) for row in rows)

    def disable(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        actor_id: str,
        expected_revision: int,
    ) -> WorkspaceRegistrationRecord:
        now = float(self._clock())
        with self._session_factory() as session:
            conditions = [
                SourceControlWorkspaceRegistrationDB.workspace_id
                == workspace_id,
                SourceControlWorkspaceRegistrationDB.tenant_id
                == tenant_id,
                SourceControlWorkspaceRegistrationDB.project_id
                == project_id,
                SourceControlWorkspaceRegistrationDB.lock_version
                == expected_revision,
                SourceControlWorkspaceRegistrationDB.registration_state
                == "active",
            ]
            if owner_id is not None:
                conditions.append(
                    SourceControlWorkspaceRegistrationDB.owner_id
                    == owner_id
                )
            result = session.exec(
                update(SourceControlWorkspaceRegistrationDB)
                .where(*conditions)
                .values(
                    registration_state="disabled",
                    lock_version=expected_revision + 1,
                    updated_at_epoch=now,
                )
            )
            if result.rowcount != 1:
                existing = session.exec(
                    select(SourceControlWorkspaceRegistrationDB).where(
                        SourceControlWorkspaceRegistrationDB.workspace_id
                        == workspace_id,
                        SourceControlWorkspaceRegistrationDB.tenant_id
                        == tenant_id,
                        SourceControlWorkspaceRegistrationDB.project_id
                        == project_id,
                    )
                ).first()
                session.rollback()
                if existing is None or (
                    owner_id is not None
                    and existing.owner_id != owner_id
                ):
                    raise SourceControlWorkspacePersistenceError(
                        "workspace_registration_not_found",
                        status_code=404,
                    )
                raise SourceControlWorkspacePersistenceError(
                    "workspace_registration_revision_conflict",
                    status_code=409,
                )
            row = session.get(
                SourceControlWorkspaceRegistrationDB,
                workspace_id,
            )
            if row is None:
                session.rollback()
                raise SourceControlWorkspacePersistenceError(
                    "workspace_registration_not_found",
                    status_code=404,
                )
            session.add(
                self._audit(
                    scope=GitSourceScope(
                        tenant_id=row.tenant_id,
                        project_id=row.project_id,
                        owner_id=actor_id,
                    ),
                    workspace_id=row.workspace_id,
                    event_type="disable",
                    decision="allow",
                    reason_code="workspace_registration_disabled",
                    now=now,
                )
            )
            session.commit()
            session.refresh(row)
            return self._registration_record(row)

    def record_denial(
        self,
        *,
        scope: GitSourceScope,
        workspace_id: str,
        event_type: str,
        reason_code: str,
    ) -> None:
        with self._session_factory() as session:
            session.add(
                self._audit(
                    scope=scope,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    decision="deny",
                    reason_code=reason_code,
                    now=float(self._clock()),
                )
            )
            session.commit()

    @staticmethod
    def _scope_matches(row: object, scope: GitSourceScope) -> bool:
        return (
            getattr(row, "tenant_id", None) == scope.tenant_id
            and getattr(row, "project_id", None) == scope.project_id
            and getattr(row, "owner_id", None) == scope.owner_id
        )

    @staticmethod
    def _validation_record(
        row: SourceControlWorkspaceValidationDB,
    ) -> WorkspaceValidationBinding:
        return WorkspaceValidationBinding(
            scope=GitSourceScope(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
            ),
            folder_handle=row.folder_handle,
            root_fingerprint=row.root_fingerprint,
            manifest_digest=row.manifest_digest,
        )

    @staticmethod
    def _registration_record(
        row: SourceControlWorkspaceRegistrationDB,
    ) -> WorkspaceRegistrationRecord:
        return WorkspaceRegistrationRecord(
            workspace_id=row.workspace_id,
            binding=WorkspaceValidationBinding(
                scope=GitSourceScope(
                    tenant_id=row.tenant_id,
                    project_id=row.project_id,
                    owner_id=row.owner_id,
                ),
                folder_handle=row.folder_handle,
                root_fingerprint=row.root_fingerprint,
                manifest_digest=row.manifest_digest,
            ),
            registration_state=row.registration_state,
            read_only=row.read_only,
            lock_version=row.lock_version,
            created_at_epoch=row.created_at_epoch,
            updated_at_epoch=row.updated_at_epoch,
        )

    def _by_validation(
        self,
        session: Session,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> WorkspaceRegistrationRecord | None:
        rows = session.exec(
            select(SourceControlWorkspaceRegistrationDB).where(
                SourceControlWorkspaceRegistrationDB.validation_handle_digest
                == handle_digest,
                SourceControlWorkspaceRegistrationDB.tenant_id
                == scope.tenant_id,
                SourceControlWorkspaceRegistrationDB.project_id
                == scope.project_id,
                SourceControlWorkspaceRegistrationDB.owner_id
                == scope.owner_id,
            )
        ).all()
        if len(rows) == 1:
            return self._registration_record(rows[0])
        validation = session.get(
            SourceControlWorkspaceValidationDB,
            handle_digest,
        )
        if (
            validation is None
            or not self._scope_matches(validation, scope)
            or not validation.workspace_id
        ):
            return None
        reused = session.get(
            SourceControlWorkspaceRegistrationDB,
            validation.workspace_id,
        )
        if reused is None or not self._scope_matches(reused, scope):
            return None
        return self._registration_record(reused)

    def _require_by_validation(
        self,
        session: Session,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> WorkspaceRegistrationRecord:
        record = self._by_validation(
            session,
            handle_digest=handle_digest,
            scope=scope,
        )
        if record is None:
            raise SourceControlWorkspacePersistenceError(
                "workspace_registration_consumption_inconsistent",
                status_code=409,
            )
        return record

    @staticmethod
    def _audit(
        *,
        scope: GitSourceScope,
        workspace_id: str,
        event_type: str,
        decision: str,
        reason_code: str,
        now: float,
    ) -> SourceControlWorkspaceRegistrationAuditDB:
        if (
            decision not in {"allow", "deny"}
            or _REASON.fullmatch(reason_code) is None
        ):
            raise SourceControlWorkspacePersistenceError(
                "workspace_registration_audit_invalid"
            )
        return SourceControlWorkspaceRegistrationAuditDB(
            audit_id=uuid.uuid4().hex,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            actor_id=scope.owner_id,
            workspace_id_digest=hashlib.sha256(
                workspace_id.encode("utf-8")
            ).hexdigest(),
            event_type=event_type,
            decision=decision,
            reason_code=reason_code,
            occurred_at_epoch=now,
        )


__all__ = [
    "SQLSourceControlWorkspaceRegistrationRepository",
    "SourceControlWorkspacePersistenceError",
]
