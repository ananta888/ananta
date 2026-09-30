"""SQL store for versioned, audited source access grants."""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.source_control import (
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
)
from agent.models.source_control_persistence import (
    SourceAccessGrantAuditRecord,
    SourceAccessGrantPreview,
    SourceAccessGrantRecord,
    SourceControlPersistenceError,
    derive_grant_family_id,
)
from agent.repositories.source_control_record_mappers import (
    grant_audit_record,
    grant_record,
    new_grant_row,
    stable_id,
)
from agent.repositories.source_control_scoped_lookups import (
    require_scoped_grant,
    require_scoped_revision,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
)

GRANT_TRANSITIONS = {
    "draft": frozenset({"active", "revoked"}),
    "active": frozenset({"superseded", "revoked"}),
    "superseded": frozenset(),
    "revoked": frozenset(),
}


class SQLSourceGrantStore:
    """Persist grant versions, state transitions, rollbacks and their audit."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._clock = clock

    def save_grant(
        self,
        contract: SourceAccessGrant,
        *,
        owner_id: str,
        grant_family_id: str,
        rollback_of_grant_id: str | None = None,
    ) -> SourceAccessGrantRecord:
        with Session(self._engine) as db:
            with db.begin():
                existing = db.get(SourceAccessGrantDB, contract.grant_id)
                if existing is not None:
                    record = grant_record(existing)
                    if (
                        record.contract != contract
                        or record.owner_id != owner_id
                        or record.grant_family_id != grant_family_id
                    ):
                        raise SourceControlPersistenceError(
                            "source_control_grant_identity_conflict"
                        )
                    return record
                require_scoped_revision(
                    db,
                    tenant_id=contract.tenant_id,
                    project_id=contract.project_id,
                    owner_id=owner_id,
                    source_revision_id=contract.source_revision_id,
                )
                latest = db.exec(
                    select(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_family_id
                        == grant_family_id
                    )
                    .order_by(SourceAccessGrantDB.grant_version.desc())
                ).first()
                required_version = 1 if latest is None else latest.grant_version + 1
                if contract.version != required_version:
                    raise SourceControlPersistenceError(
                        "source_control_grant_version_invalid"
                    )
                now = float(self._clock())
                row = new_grant_row(
                    contract,
                    owner_id=owner_id,
                    grant_family_id=grant_family_id,
                    rollback_of_grant_id=rollback_of_grant_id,
                    updated_at_epoch=now,
                )
                db.add(row)
                self._add_grant_audit(
                    db,
                    row=row,
                    action="create",
                    from_state=None,
                    to_state=contract.state.value,
                    reason_code="grant_created",
                    grant_lock_version=1,
                    occurred_at_epoch=now,
                )
            return grant_record(row)

    def preview_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        at_epoch: float,
    ) -> SourceAccessGrantPreview:
        with Session(self._engine) as db:
            with db.begin():
                row = require_scoped_grant(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    grant_id=grant_id,
                )
                bindings_match = (
                    row.source_revision_id == source_revision_id
                    and row.destination_id == destination_id
                    and row.operation == operation.value
                    and row.transformation == transformation.value
                )
                active_state = row.state in {"draft", "active"}
                unexpired = row.expires_at_epoch > at_epoch
                allowed = bindings_match and active_state and unexpired
                if not bindings_match:
                    reason = "grant_binding_mismatch"
                elif not active_state:
                    reason = "grant_not_usable"
                elif not unexpired:
                    reason = "grant_expired"
                else:
                    reason = "grant_preview_allowed"
                self._add_grant_audit(
                    db,
                    row=row,
                    action="preview",
                    from_state=row.state,
                    to_state=row.state,
                    reason_code=reason,
                    grant_lock_version=row.lock_version,
                    occurred_at_epoch=at_epoch,
                )
            return SourceAccessGrantPreview(
                grant_id=row.grant_id,
                allowed=allowed,
                reason_code=reason,
                source_revision_id=source_revision_id,
                destination_id=destination_id,
                operation=operation.value,
                transformation=transformation.value,
                lock_version=row.lock_version,
            )

    def transition_grant(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        grant_id: str,
        target_state: GrantState,
        expected_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        target = target_state.value
        with Session(self._engine) as db:
            with db.begin():
                row = require_scoped_grant(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    grant_id=grant_id,
                )
                if row.lock_version != expected_lock_version:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                if target not in GRANT_TRANSITIONS[row.state]:
                    raise SourceControlPersistenceError(
                        "source_control_grant_transition_invalid"
                    )
                previous = row.state
                now = float(self._clock())
                result = db.execute(
                    update(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_id == grant_id,
                        SourceAccessGrantDB.lock_version
                        == expected_lock_version,
                    )
                    .values(
                        state=target,
                        lock_version=expected_lock_version + 1,
                        updated_at_epoch=now,
                    )
                )
                if result.rowcount != 1:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                row.state = target
                row.lock_version = expected_lock_version + 1
                row.updated_at_epoch = now
                self._add_grant_audit(
                    db,
                    row=row,
                    action=target,
                    from_state=previous,
                    to_state=target,
                    reason_code=reason_code,
                    grant_lock_version=row.lock_version,
                    occurred_at_epoch=now,
                )
            return grant_record(row)

    def rollback_grant(
        self,
        *,
        previous_grant_id: str,
        replacement: SourceAccessGrant,
        owner_id: str,
        grant_family_id: str,
        expected_previous_lock_version: int,
        reason_code: str,
    ) -> SourceAccessGrantRecord:
        with Session(self._engine) as db:
            with db.begin():
                previous = require_scoped_grant(
                    db,
                    tenant_id=replacement.tenant_id,
                    project_id=replacement.project_id,
                    owner_id=owner_id,
                    grant_id=previous_grant_id,
                )
                if (
                    previous.lock_version != expected_previous_lock_version
                    or previous.state not in {"superseded", "revoked"}
                    or previous.grant_family_id != grant_family_id
                    or derive_grant_family_id(replacement)
                    != grant_family_id
                    or replacement.state is not GrantState.ACTIVE
                ):
                    raise SourceControlPersistenceError(
                        "source_control_grant_rollback_invalid"
                    )
                latest = db.exec(
                    select(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.grant_family_id
                        == grant_family_id
                    )
                    .order_by(SourceAccessGrantDB.grant_version.desc())
                ).first()
                if (
                    latest is None
                    or replacement.version != latest.grant_version + 1
                ):
                    raise SourceControlPersistenceError(
                        "source_control_grant_version_invalid"
                    )
                require_scoped_revision(
                    db,
                    tenant_id=replacement.tenant_id,
                    project_id=replacement.project_id,
                    owner_id=owner_id,
                    source_revision_id=replacement.source_revision_id,
                )
                now = float(self._clock())
                row = new_grant_row(
                    replacement,
                    owner_id=owner_id,
                    grant_family_id=grant_family_id,
                    rollback_of_grant_id=previous_grant_id,
                    updated_at_epoch=now,
                )
                db.add(row)
                self._add_grant_audit(
                    db,
                    row=row,
                    action="rollback",
                    from_state=previous.state,
                    to_state="active",
                    reason_code=reason_code,
                    grant_lock_version=1,
                    occurred_at_epoch=now,
                )
            return grant_record(row)

    def list_grant_audit(
        self, *, grant_id: str
    ) -> tuple[SourceAccessGrantAuditRecord, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(SourceAccessGrantAuditDB)
                .where(SourceAccessGrantAuditDB.grant_id == grant_id)
                .order_by(
                    SourceAccessGrantAuditDB.occurred_at_epoch,
                    SourceAccessGrantAuditDB.audit_id,
                )
            ).all()
            records = tuple(grant_audit_record(row) for row in rows)
            return tuple(
                sorted(
                    records,
                    key=lambda record: (
                        record.grant_lock_version,
                        1 if record.action == "preview" else 0,
                        record.occurred_at_epoch,
                        record.audit_id,
                    ),
                )
            )

    def _add_grant_audit(
        self,
        db: Session,
        *,
        row: SourceAccessGrantDB,
        action: str,
        from_state: str | None,
        to_state: str | None,
        reason_code: str,
        grant_lock_version: int,
        occurred_at_epoch: float,
    ) -> None:
        audit_id = stable_id(
            "audit",
            {
                "action": action,
                "grant_id": row.grant_id,
                "grant_lock_version": grant_lock_version,
                "reason_code": reason_code,
            },
        )
        if db.get(SourceAccessGrantAuditDB, audit_id) is not None:
            return
        db.add(
            SourceAccessGrantAuditDB(
                audit_id=audit_id,
                grant_id=row.grant_id,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                owner_id=row.owner_id,
                action=action,
                from_state=from_state,
                to_state=to_state,
                reason_code=reason_code,
                grant_lock_version=grant_lock_version,
                occurred_at_epoch=occurred_at_epoch,
            )
        )
