"""Hub-owned administration of scoped source-access grants.

The service deliberately accepts only opaque source, destination, policy, and
preset identifiers. Tenant scope, execution coordinates, policy snapshots,
grant identities, version numbers, and audit identities are all resolved or
derived by the Hub.

``SourceControlGrantAdminService`` sequences the grant transactions and
composes focused collaborators (each a keyword-only constructor seam with a
production default):

* ``SourceControlGrantPresetCatalog`` -- reviewed grant shapes;
* ``GrantCreationPolicyGate`` -- scoped destination + active policy decision;
* ``GrantMutationReceipts`` -- idempotency identity and receipt replay.

Contracts live in ``source_control_grant_admin_contracts`` and pure rules in
``source_control_grant_admin_rules``; all public names stay importable here.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_access_enforcement import (
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.services.context_policy_lifecycle import ContextPolicyVersion
from agent.services.source_access_enforcement import source_access_grant_digest
from agent.services.source_control_grant_admin_contracts import (
    ActiveContextPolicyPort,
    GrantAdminActor,
    GrantCreateRequest,
    GrantListPage,
    GrantPreset,
    GrantRevokeRequest,
    GrantView,
    ScopedDestinationCatalogPort,
    SourceControlGrantAdminError,
)
from agent.services.source_control_grant_admin_receipts import (
    GrantMutationReceipts,
    grant_audit_row,
    grant_operation_row,
)
from agent.services.source_control_grant_admin_rules import (
    DESTINATION_ID,
    GRANT_ID,
    GRANT_STATES,
    OPAQUE_ID,
    SOURCE_REVISION_ID,
    decode_cursor,
    encode_cursor,
    grant_etag,
    grant_family_id,
    iso_timestamp,
    normalize_etag,
    policy_snapshot_id,
    require_mutator,
    require_pattern,
    validate_actor,
    validate_create_request,
)
from agent.services.source_control_grant_policy_gate import (
    GrantCreationPolicyGate,
)
from agent.services.source_control_grant_presets import (
    SourceControlGrantPresetCatalog,
)
from agent.services.source_destination_resolution import (
    source_destination_digest,
)
from ananta_contracts.source_control import SourceAccessGrant


class SourceControlGrantAdminService:
    """Transactional grant administration owned by the Hub control plane."""

    def __init__(
        self,
        *,
        engine: Engine,
        destinations: ScopedDestinationCatalogPort,
        policies: ActiveContextPolicyPort,
        presets: SourceControlGrantPresetCatalog | None = None,
        clock=time.time,
        policy_gate: GrantCreationPolicyGate | None = None,
        receipts: GrantMutationReceipts | None = None,
    ) -> None:
        self._engine = engine
        self._presets = presets or SourceControlGrantPresetCatalog()
        self._clock = clock
        self._policy_gate = policy_gate or GrantCreationPolicyGate(
            destinations=destinations,
            policies=policies,
        )
        self._receipts = receipts or GrantMutationReceipts(engine=engine)

    def list_presets(
        self, *, actor: GrantAdminActor
    ) -> tuple[GrantPreset, ...]:
        validate_actor(actor)
        return self._presets.list()

    def create_grant(
        self,
        *,
        actor: GrantAdminActor,
        request: GrantCreateRequest,
        if_match: str,
        idempotency_key: str,
    ) -> GrantView:
        require_mutator(actor)
        validate_create_request(request)
        normalized_etag = normalize_etag(if_match)
        preset = self._presets.get(request.preset_id)
        if preset is None:
            raise SourceControlGrantAdminError(
                "grant_preset_not_found", status_code=404
            )
        if (
            request.duration_seconds < 60
            or request.duration_seconds > preset.max_duration_seconds
        ):
            raise SourceControlGrantAdminError(
                "grant_duration_invalid", status_code=400
            )
        operation_key, request_digest = self._receipts.mutation_identity(
            actor=actor,
            operation="grant_create",
            idempotency_key=idempotency_key,
            payload={
                "source_revision_id": request.source_revision_id,
                "destination_id": request.destination_id,
                "policy_id": request.policy_id,
                "preset_id": request.preset_id,
                "duration_seconds": request.duration_seconds,
                "if_match": normalized_etag,
            },
        )
        replay = self._receipts.replay(
            operation_key=operation_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay

        destination, policy = self._policy_gate.authorize(
            actor=actor,
            request=request,
            preset=preset,
            normalized_etag=normalized_etag,
        )

        now = float(self._clock())
        policy_version = policy_snapshot_id(policy)
        family_id = grant_family_id(
            actor=actor,
            request=request,
            preset=preset,
            policy_version=policy_version,
        )
        try:
            with Session(self._engine) as db:
                self._require_source_revision(
                    db=db,
                    actor=actor,
                    source_revision_id=request.source_revision_id,
                )
                self._require_current_policy(
                    db=db,
                    actor=actor,
                    policy=policy,
                    expected_etag=normalized_etag,
                )
                latest = db.exec(
                    select(SourceAccessGrantDB)
                    .where(
                        SourceAccessGrantDB.tenant_id == actor.tenant_id,
                        SourceAccessGrantDB.project_id == actor.project_id,
                        SourceAccessGrantDB.grant_family_id == family_id,
                    )
                    .order_by(SourceAccessGrantDB.grant_version.desc())
                    .limit(1)
                ).first()
                version = (latest.grant_version if latest else 0) + 1
                if latest is not None and latest.state == "active":
                    mutation = db.exec(
                        update(SourceAccessGrantDB)
                        .where(
                            SourceAccessGrantDB.grant_id
                            == latest.grant_id,
                            SourceAccessGrantDB.tenant_id
                            == actor.tenant_id,
                            SourceAccessGrantDB.project_id
                            == actor.project_id,
                            SourceAccessGrantDB.lock_version
                            == latest.lock_version,
                            SourceAccessGrantDB.state == "active",
                        )
                        .values(
                            state="superseded",
                            lock_version=latest.lock_version + 1,
                            updated_at_epoch=now,
                        )
                    )
                    if mutation.rowcount != 1:
                        db.rollback()
                        replay = self._receipts.replay(
                            operation_key=operation_key,
                            request_digest=request_digest,
                        )
                        if replay is not None:
                            return replay
                        raise SourceControlGrantAdminError(
                            "grant_version_conflict", status_code=409
                        )
                    db.add(
                        grant_audit_row(
                            row=latest,
                            actor=actor,
                            action="supersede",
                            from_state="active",
                            to_state="superseded",
                            reason_code="grant_renewed",
                            lock_version=latest.lock_version + 1,
                            occurred_at=now,
                        )
                    )
                issued_at = datetime.fromtimestamp(now, tz=timezone.utc)
                expires_at = datetime.fromtimestamp(
                    now + request.duration_seconds, tz=timezone.utc
                )
                contract = SourceAccessGrant.create(
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    source_revision_id=request.source_revision_id,
                    destination_id=destination.destination_id,
                    operation=preset.operation,
                    transformation=preset.transformation,
                    purpose=preset.purpose,
                    policy_version=policy_version,
                    policy_snapshot_digest=policy.policy_digest,
                    state="active",
                    issued_at=issued_at,
                    expires_at=expires_at,
                    version=version,
                )
                row = SourceAccessGrantDB(
                    grant_id=contract.grant_id,
                    grant_family_id=family_id,
                    grant_version=version,
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    owner_id=actor.subject_id,
                    source_revision_id=request.source_revision_id,
                    destination_id=destination.destination_id,
                    operation=preset.operation.value,
                    transformation=preset.transformation.value,
                    purpose=preset.purpose,
                    policy_version=policy_version,
                    policy_snapshot_digest=policy.policy_digest,
                    state="active",
                    issued_at_epoch=now,
                    expires_at_epoch=now + request.duration_seconds,
                    lock_version=1,
                    updated_at_epoch=now,
                )
                db.add(row)
                db.add(
                    SourceAccessGrantExecutionPolicyDB(
                        grant_id=contract.grant_id,
                        grant_digest=source_access_grant_digest(contract),
                        destination_digest=source_destination_digest(
                            destination
                        ),
                        consumption_mode=preset.consumption_mode,
                        grant_lock_version=1,
                        concurrency_version=1,
                        created_at=issued_at,
                        updated_at=issued_at,
                    )
                )
                db.add(
                    grant_audit_row(
                        row=row,
                        actor=actor,
                        action="create",
                        from_state=None,
                        to_state="active",
                        reason_code="grant_created",
                        lock_version=1,
                        occurred_at=now,
                    )
                )
                result = self._view(row=row, now=now)
                db.add(
                    grant_operation_row(
                        operation_key=operation_key,
                        request_digest=request_digest,
                        operation="grant_create",
                        result=result,
                        occurred_at=now,
                    )
                )
                db.commit()
                return result
        except IntegrityError as exc:
            replay = self._receipts.replay(
                operation_key=operation_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            raise SourceControlGrantAdminError(
                "grant_version_conflict", status_code=409
            ) from exc

    def list_grants(
        self,
        *,
        actor: GrantAdminActor,
        cursor: str | None = None,
        limit: int = 50,
        state: str | None = None,
        source_revision_id: str | None = None,
        destination_id: str | None = None,
    ) -> GrantListPage:
        validate_actor(actor)
        if limit < 1 or limit > 200:
            raise SourceControlGrantAdminError(
                "grant_limit_invalid", status_code=400
            )
        if state is not None and state not in GRANT_STATES:
            raise SourceControlGrantAdminError(
                "grant_state_invalid", status_code=400
            )
        if source_revision_id is not None:
            require_pattern(
                source_revision_id,
                SOURCE_REVISION_ID,
                "grant_source_revision_invalid",
            )
        if destination_id is not None:
            require_pattern(
                destination_id,
                DESTINATION_ID,
                "grant_destination_invalid",
            )
        after = decode_cursor(cursor)
        statement = select(SourceAccessGrantDB).where(
            SourceAccessGrantDB.tenant_id == actor.tenant_id,
            SourceAccessGrantDB.project_id == actor.project_id,
        )
        if after is not None:
            statement = statement.where(
                SourceAccessGrantDB.grant_id > after
            )
        if state is not None:
            statement = statement.where(
                SourceAccessGrantDB.state == state
            )
        if source_revision_id is not None:
            statement = statement.where(
                SourceAccessGrantDB.source_revision_id
                == source_revision_id
            )
        if destination_id is not None:
            statement = statement.where(
                SourceAccessGrantDB.destination_id == destination_id
            )
        with Session(self._engine) as db:
            selected = list(
                db.exec(
                    statement.order_by(SourceAccessGrantDB.grant_id).limit(
                        limit + 1
                    )
                ).all()
            )
        visible = selected[:limit]
        now = float(self._clock())
        return GrantListPage(
            items=tuple(self._view(row=row, now=now) for row in visible),
            next_cursor=(
                encode_cursor(visible[-1].grant_id)
                if len(selected) > limit and visible
                else None
            ),
        )

    def revoke_grant(
        self,
        *,
        actor: GrantAdminActor,
        grant_id: str,
        request: GrantRevokeRequest,
        if_match: str,
        idempotency_key: str,
    ) -> GrantView:
        require_mutator(actor)
        require_pattern(grant_id, GRANT_ID, "grant_id_invalid")
        if (
            not OPAQUE_ID.fullmatch(request.reason_code)
            or len(request.reason_code) > 128
        ):
            raise SourceControlGrantAdminError(
                "grant_revoke_reason_invalid", status_code=400
            )
        normalized_etag = normalize_etag(if_match)
        operation_key, request_digest = self._receipts.mutation_identity(
            actor=actor,
            operation="grant_revoke",
            idempotency_key=idempotency_key,
            payload={
                "grant_id": grant_id,
                "reason_code": request.reason_code,
                "if_match": normalized_etag,
            },
        )
        replay = self._receipts.replay(
            operation_key=operation_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay
        now = float(self._clock())
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceAccessGrantDB).where(
                    SourceAccessGrantDB.grant_id == grant_id,
                    SourceAccessGrantDB.tenant_id == actor.tenant_id,
                    SourceAccessGrantDB.project_id == actor.project_id,
                )
            ).first()
            if row is None:
                raise SourceControlGrantAdminError(
                    "grant_not_found", status_code=404
                )
            if row.state != "active":
                raise SourceControlGrantAdminError(
                    "grant_not_active", status_code=409
                )
            if normalized_etag != grant_etag(row):
                raise SourceControlGrantAdminError(
                    "grant_version_conflict", status_code=412
                )
            next_version = row.lock_version + 1
            mutation = db.exec(
                update(SourceAccessGrantDB)
                .where(
                    SourceAccessGrantDB.grant_id == grant_id,
                    SourceAccessGrantDB.tenant_id == actor.tenant_id,
                    SourceAccessGrantDB.project_id == actor.project_id,
                    SourceAccessGrantDB.lock_version == row.lock_version,
                    SourceAccessGrantDB.state == "active",
                )
                .values(
                    state="revoked",
                    lock_version=next_version,
                    updated_at_epoch=now,
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                replay = self._receipts.replay(
                    operation_key=operation_key,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return replay
                raise SourceControlGrantAdminError(
                    "grant_version_conflict", status_code=412
                )
            db.add(
                grant_audit_row(
                    row=row,
                    actor=actor,
                    action="revoke",
                    from_state="active",
                    to_state="revoked",
                    reason_code=request.reason_code,
                    lock_version=next_version,
                    occurred_at=now,
                )
            )
            db.expire(row)
            db.refresh(row)
            result = self._view(row=row, now=now)
            db.add(
                grant_operation_row(
                    operation_key=operation_key,
                    request_digest=request_digest,
                    operation="grant_revoke",
                    result=result,
                    occurred_at=now,
                )
            )
            try:
                db.commit()
                return result
            except IntegrityError as exc:
                db.rollback()
                replay = self._receipts.replay(
                    operation_key=operation_key,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return replay
                raise SourceControlGrantAdminError(
                    "grant_version_conflict", status_code=409
                ) from exc

    def _require_source_revision(
        self,
        *,
        db: Session,
        actor: GrantAdminActor,
        source_revision_id: str,
    ) -> None:
        revision = db.exec(
            select(SourceRevisionDB).where(
                SourceRevisionDB.source_revision_id
                == source_revision_id,
                SourceRevisionDB.tenant_id == actor.tenant_id,
                SourceRevisionDB.project_id == actor.project_id,
                SourceRevisionDB.admission_state == "admitted",
            )
        ).first()
        if revision is None:
            raise SourceControlGrantAdminError(
                "grant_resource_not_found", status_code=404
            )
        connection = db.exec(
            select(SourceConnectionDB).where(
                SourceConnectionDB.connection_id
                == revision.connection_id,
                SourceConnectionDB.tenant_id == actor.tenant_id,
                SourceConnectionDB.project_id == actor.project_id,
                SourceConnectionDB.state == "active",
            )
        ).first()
        if connection is None:
            raise SourceControlGrantAdminError(
                "grant_resource_not_found", status_code=404
            )

    @staticmethod
    def _require_current_policy(
        *,
        db: Session,
        actor: GrantAdminActor,
        policy: ContextPolicyVersion,
        expected_etag: str,
    ) -> None:
        current = db.exec(
            select(ContextPolicyVersionDB).where(
                ContextPolicyVersionDB.tenant_id == actor.tenant_id,
                ContextPolicyVersionDB.project_id == actor.project_id,
                ContextPolicyVersionDB.policy_id == policy.policy_id,
                ContextPolicyVersionDB.version == policy.version,
                ContextPolicyVersionDB.state == "active",
                ContextPolicyVersionDB.etag == expected_etag,
                ContextPolicyVersionDB.policy_digest
                == policy.policy_digest,
            )
        ).first()
        if current is None:
            raise SourceControlGrantAdminError(
                "grant_policy_version_conflict", status_code=412
            )

    def _view(self, *, row: SourceAccessGrantDB, now: float) -> GrantView:
        return GrantView(
            grant_id=row.grant_id,
            grant_family_id=row.grant_family_id,
            version=row.grant_version,
            source_revision_id=row.source_revision_id,
            destination_id=row.destination_id,
            preset_id=self._presets.matching_id(
                operation=row.operation,
                transformation=row.transformation,
                purpose=row.purpose,
            ),
            operation=row.operation,
            transformation=row.transformation,
            purpose=row.purpose,
            policy_version=row.policy_version,
            state=row.state,
            issued_at=iso_timestamp(row.issued_at_epoch),
            expires_at=iso_timestamp(row.expires_at_epoch),
            expired=now >= row.expires_at_epoch,
            etag=grant_etag(row),
        )


__all__ = [
    "ActiveContextPolicyPort",
    "GrantAdminActor",
    "GrantCreateRequest",
    "GrantListPage",
    "GrantPreset",
    "GrantRevokeRequest",
    "GrantView",
    "ScopedDestinationCatalogPort",
    "SourceControlGrantAdminError",
    "SourceControlGrantAdminService",
    "SourceControlGrantPresetCatalog",
]
