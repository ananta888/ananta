"""Hub-owned administration of scoped source-access grants.

The service deliberately accepts only opaque source, destination, policy, and
preset identifiers. Tenant scope, execution coordinates, policy snapshots,
grant identities, version numbers, and audit identities are all resolved or
derived by the Hub.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_access_enforcement import (
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_control import (
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceControlOperationDB,
    SourceRevisionDB,
)
from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyLifecycleError,
    ContextPolicyPreview,
    ContextPolicyVersion,
)
from agent.services.source_access_enforcement import source_access_grant_digest
from agent.services.source_destination_resolution import (
    source_destination_digest,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    GrantOperation,
    GrantTransformation,
    SourceAccessGrant,
)


_SCHEMA_GRANT = "ananta.source-control.grant-admin-item.v1"
_SCHEMA_GRANT_LIST = "ananta.source-control.grant-admin-list.v1"
_SCHEMA_PRESET = "ananta.source-control.grant-preset.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION_ID = re.compile(r"^srev_[0-9a-f]{64}$")
_DESTINATION_ID = re.compile(r"^dst_[0-9a-f]{64}$")
_GRANT_ID = re.compile(r"^grant_[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")
_MUTATOR_ROLES = frozenset({"admin", "project_owner"})
_GRANT_STATES = frozenset({"draft", "active", "superseded", "revoked"})


class SourceControlGrantAdminError(ValueError):
    """Stable service-boundary error without persistence detail leakage."""

    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class GrantAdminActor:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class GrantCreateRequest:
    source_revision_id: str
    destination_id: str
    policy_id: str
    preset_id: str
    duration_seconds: int

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "GrantCreateRequest":
        expected = {
            "source_revision_id",
            "destination_id",
            "policy_id",
            "preset_id",
            "duration_seconds",
        }
        if set(payload) != expected:
            raise SourceControlGrantAdminError(
                "grant_create_fields_invalid", status_code=400
            )
        duration = payload["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise SourceControlGrantAdminError(
                "grant_duration_invalid", status_code=400
            )
        return cls(
            source_revision_id=str(payload["source_revision_id"]),
            destination_id=str(payload["destination_id"]),
            policy_id=str(payload["policy_id"]),
            preset_id=str(payload["preset_id"]),
            duration_seconds=duration,
        )


@dataclass(frozen=True)
class GrantRevokeRequest:
    reason_code: str

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "GrantRevokeRequest":
        if set(payload) != {"reason_code"}:
            raise SourceControlGrantAdminError(
                "grant_revoke_fields_invalid", status_code=400
            )
        return cls(reason_code=str(payload["reason_code"]))


@dataclass(frozen=True)
class GrantPreset:
    preset_id: str
    label: str
    description: str
    operation: GrantOperation
    transformation: GrantTransformation
    purpose: str
    consumption_mode: str
    max_duration_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA_PRESET,
            "preset_id": self.preset_id,
            "label": self.label,
            "description": self.description,
            "operation": self.operation.value,
            "transformation": self.transformation.value,
            "purpose": self.purpose,
            "max_duration_seconds": self.max_duration_seconds,
        }


@dataclass(frozen=True)
class GrantView:
    grant_id: str
    grant_family_id: str
    version: int
    source_revision_id: str
    destination_id: str
    preset_id: str | None
    operation: str
    transformation: str
    purpose: str
    policy_version: str
    state: str
    issued_at: str
    expires_at: str
    expired: bool
    etag: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA_GRANT,
            "grant_id": self.grant_id,
            "grant_family_id": self.grant_family_id,
            "version": self.version,
            "source_revision_id": self.source_revision_id,
            "destination_id": self.destination_id,
            "preset_id": self.preset_id,
            "operation": self.operation,
            "transformation": self.transformation,
            "purpose": self.purpose,
            "policy_version": self.policy_version,
            "state": self.state,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "expired": self.expired,
            "etag": self.etag,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "GrantView":
        if payload.get("schema") != _SCHEMA_GRANT:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            )
        try:
            return cls(
                grant_id=str(payload["grant_id"]),
                grant_family_id=str(payload["grant_family_id"]),
                version=int(payload["version"]),
                source_revision_id=str(payload["source_revision_id"]),
                destination_id=str(payload["destination_id"]),
                preset_id=(
                    str(payload["preset_id"])
                    if payload.get("preset_id") is not None
                    else None
                ),
                operation=str(payload["operation"]),
                transformation=str(payload["transformation"]),
                purpose=str(payload["purpose"]),
                policy_version=str(payload["policy_version"]),
                state=str(payload["state"]),
                issued_at=str(payload["issued_at"]),
                expires_at=str(payload["expires_at"]),
                expired=bool(payload["expired"]),
                etag=str(payload["etag"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            ) from exc


@dataclass(frozen=True)
class GrantListPage:
    items: tuple[GrantView, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA_GRANT_LIST,
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
        }


class ScopedDestinationCatalogPort(Protocol):
    """Implemented by ScopedWorkerModelDestinationCatalog."""

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> DestinationDescriptor | None: ...


class ActiveContextPolicyPort(Protocol):
    """Subset implemented by the persistent Context Policy lifecycle."""

    def active(
        self, *, actor: ContextPolicyActor, policy_id: str
    ) -> ContextPolicyVersion: ...

    def preview(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
    ) -> ContextPolicyPreview: ...


class SourceControlGrantPresetCatalog:
    """Immutable, read-only catalog of reviewed grant shapes."""

    _PRESETS = (
        GrantPreset(
            preset_id="worker_index_redacted",
            label="Redacted worker index",
            description="Index redacted source material on an authorized worker.",
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
            purpose="knowledge-index",
            consumption_mode="one_time",
            max_duration_seconds=86_400,
        ),
        GrantPreset(
            preset_id="chat_context_redacted",
            label="Redacted chat context",
            description="Provide redacted source context to an authorized model.",
            operation=GrantOperation.CHAT_CONTEXT,
            transformation=GrantTransformation.REDACTED,
            purpose="assisted_code_review",
            consumption_mode="reusable",
            max_duration_seconds=14_400,
        ),
        GrantPreset(
            preset_id="tool_read_summary",
            label="Summary-only tool read",
            description="Expose source summaries to an authorized tool target.",
            operation=GrantOperation.EXPORT,
            transformation=GrantTransformation.SUMMARY,
            purpose="tool_assisted_review",
            consumption_mode="reusable",
            max_duration_seconds=3_600,
        ),
    )

    def list(self) -> tuple[GrantPreset, ...]:
        return self._PRESETS

    def get(self, preset_id: str) -> GrantPreset | None:
        return next(
            (
                preset
                for preset in self._PRESETS
                if preset.preset_id == preset_id
            ),
            None,
        )

    def matching_id(
        self, *, operation: str, transformation: str, purpose: str
    ) -> str | None:
        return next(
            (
                preset.preset_id
                for preset in self._PRESETS
                if preset.operation.value == operation
                and preset.transformation.value == transformation
                and preset.purpose == purpose
            ),
            None,
        )


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
    ) -> None:
        self._engine = engine
        self._destinations = destinations
        self._policies = policies
        self._presets = presets or SourceControlGrantPresetCatalog()
        self._clock = clock

    def list_presets(
        self, *, actor: GrantAdminActor
    ) -> tuple[GrantPreset, ...]:
        _validate_actor(actor)
        return self._presets.list()

    def create_grant(
        self,
        *,
        actor: GrantAdminActor,
        request: GrantCreateRequest,
        if_match: str,
        idempotency_key: str,
    ) -> GrantView:
        _require_mutator(actor)
        _validate_create_request(request)
        normalized_etag = _normalize_etag(if_match)
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
        operation_key, request_digest = self._mutation_identity(
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
        replay = self._replay(
            operation_key=operation_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay

        destination = self._destinations.get(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            destination_id=request.destination_id,
        )
        if (
            destination is None
            or destination.destination_id != request.destination_id
        ):
            raise SourceControlGrantAdminError(
                "grant_resource_not_found", status_code=404
            )
        policy_actor = _policy_actor(actor)
        try:
            policy = self._policies.active(
                actor=policy_actor, policy_id=request.policy_id
            )
        except ContextPolicyLifecycleError as exc:
            raise SourceControlGrantAdminError(
                "grant_active_policy_missing", status_code=409
            ) from exc
        if (
            policy.tenant_id != actor.tenant_id
            or policy.project_id != actor.project_id
            or policy.policy_id != request.policy_id
            or policy.state != "active"
        ):
            raise SourceControlGrantAdminError(
                "grant_active_policy_missing", status_code=409
            )
        if normalized_etag != policy.etag:
            raise SourceControlGrantAdminError(
                "grant_policy_version_conflict", status_code=412
            )
        try:
            preview = self._policies.preview(
                actor=policy_actor,
                policy_id=policy.policy_id,
                version=policy.version,
                source_revision_id=request.source_revision_id,
                destination_id=destination.destination_id,
                operation=preset.operation,
                transformation=preset.transformation,
            )
        except ContextPolicyLifecycleError as exc:
            raise SourceControlGrantAdminError(
                "grant_policy_evaluation_failed", status_code=409
            ) from exc
        if preview.policy_digest != policy.policy_digest:
            raise SourceControlGrantAdminError(
                "grant_policy_snapshot_mismatch", status_code=409
            )
        if not _preview_allows_transformation(
            decision=preview.decision,
            transformation=preset.transformation,
        ):
            reason = (
                "grant_policy_approval_required"
                if preview.decision == "approval_required"
                else "grant_policy_denied"
            )
            raise SourceControlGrantAdminError(reason, status_code=403)

        now = float(self._clock())
        policy_version = _policy_snapshot_id(policy)
        family_id = _grant_family_id(
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
                        replay = self._replay(
                            operation_key=operation_key,
                            request_digest=request_digest,
                        )
                        if replay is not None:
                            return replay
                        raise SourceControlGrantAdminError(
                            "grant_version_conflict", status_code=409
                        )
                    db.add(
                        _audit_row(
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
                    _audit_row(
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
                    _operation_row(
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
            replay = self._replay(
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
        _validate_actor(actor)
        if limit < 1 or limit > 200:
            raise SourceControlGrantAdminError(
                "grant_limit_invalid", status_code=400
            )
        if state is not None and state not in _GRANT_STATES:
            raise SourceControlGrantAdminError(
                "grant_state_invalid", status_code=400
            )
        if source_revision_id is not None:
            _require_pattern(
                source_revision_id,
                _SOURCE_REVISION_ID,
                "grant_source_revision_invalid",
            )
        if destination_id is not None:
            _require_pattern(
                destination_id,
                _DESTINATION_ID,
                "grant_destination_invalid",
            )
        after = _decode_cursor(cursor)
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
                _encode_cursor(visible[-1].grant_id)
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
        _require_mutator(actor)
        _require_pattern(grant_id, _GRANT_ID, "grant_id_invalid")
        if (
            not _OPAQUE_ID.fullmatch(request.reason_code)
            or len(request.reason_code) > 128
        ):
            raise SourceControlGrantAdminError(
                "grant_revoke_reason_invalid", status_code=400
            )
        normalized_etag = _normalize_etag(if_match)
        operation_key, request_digest = self._mutation_identity(
            actor=actor,
            operation="grant_revoke",
            idempotency_key=idempotency_key,
            payload={
                "grant_id": grant_id,
                "reason_code": request.reason_code,
                "if_match": normalized_etag,
            },
        )
        replay = self._replay(
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
            if normalized_etag != _grant_etag(row):
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
                replay = self._replay(
                    operation_key=operation_key,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return replay
                raise SourceControlGrantAdminError(
                    "grant_version_conflict", status_code=412
                )
            db.add(
                _audit_row(
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
                _operation_row(
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
                replay = self._replay(
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
            issued_at=_iso(row.issued_at_epoch),
            expires_at=_iso(row.expires_at_epoch),
            expired=now >= row.expires_at_epoch,
            etag=_grant_etag(row),
        )

    def _mutation_identity(
        self,
        *,
        actor: GrantAdminActor,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> tuple[str, str]:
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 255:
            raise SourceControlGrantAdminError(
                "grant_idempotency_key_required", status_code=428
            )
        request_digest = _digest(
            {
                "schema": "ananta.source-control.grant-mutation.v1",
                "operation": operation,
                "actor_id": actor.subject_id,
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "payload": dict(payload),
            }
        )
        operation_key = "gadm_" + _digest(
            {
                "operation": operation,
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "idempotency_key": key,
            }
        )
        return operation_key, request_digest

    def _replay(
        self, *, operation_key: str, request_digest: str
    ) -> GrantView | None:
        with Session(self._engine) as db:
            receipt = db.get(SourceControlOperationDB, operation_key)
        if receipt is None:
            return None
        if receipt.request_digest != request_digest:
            raise SourceControlGrantAdminError(
                "grant_idempotency_key_conflict", status_code=409
            )
        if receipt.state != "completed" or not receipt.result_json:
            raise SourceControlGrantAdminError(
                "grant_mutation_in_progress", status_code=409
            )
        try:
            payload = json.loads(receipt.result_json)
        except (TypeError, ValueError) as exc:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            ) from exc
        if not isinstance(payload, Mapping):
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            )
        return GrantView.from_dict(payload)


def _validate_actor(actor: GrantAdminActor) -> None:
    for value in (actor.subject_id, actor.tenant_id, actor.project_id):
        if not _OPAQUE_ID.fullmatch(str(value)) or len(str(value)) > 128:
            raise SourceControlGrantAdminError(
                "grant_actor_invalid", status_code=400
            )


def _require_mutator(actor: GrantAdminActor) -> None:
    _validate_actor(actor)
    if not (_MUTATOR_ROLES & actor.roles):
        raise SourceControlGrantAdminError(
            "grant_admin_required", status_code=403
        )


def _validate_create_request(request: GrantCreateRequest) -> None:
    _require_pattern(
        request.source_revision_id,
        _SOURCE_REVISION_ID,
        "grant_source_revision_invalid",
    )
    _require_pattern(
        request.destination_id,
        _DESTINATION_ID,
        "grant_destination_invalid",
    )
    if not _OPAQUE_ID.fullmatch(request.policy_id):
        raise SourceControlGrantAdminError(
            "grant_policy_id_invalid", status_code=400
        )
    if not _OPAQUE_ID.fullmatch(request.preset_id):
        raise SourceControlGrantAdminError(
            "grant_preset_id_invalid", status_code=400
        )
    if (
        isinstance(request.duration_seconds, bool)
        or not isinstance(request.duration_seconds, int)
    ):
        raise SourceControlGrantAdminError(
            "grant_duration_invalid", status_code=400
        )


def _require_pattern(
    value: str, pattern: re.Pattern[str], reason_code: str
) -> None:
    if not pattern.fullmatch(str(value)):
        raise SourceControlGrantAdminError(reason_code, status_code=400)


def _policy_actor(actor: GrantAdminActor) -> ContextPolicyActor:
    return ContextPolicyActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


def _normalize_etag(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    if not _SHA256.fullmatch(normalized):
        raise SourceControlGrantAdminError(
            "grant_if_match_invalid", status_code=428
        )
    return normalized


def _policy_snapshot_id(policy: ContextPolicyVersion) -> str:
    return "cpv_" + _digest(
        {
            "tenant_id": policy.tenant_id,
            "project_id": policy.project_id,
            "policy_id": policy.policy_id,
            "version": policy.version,
            "policy_digest": policy.policy_digest,
        }
    )


def _preview_allows_transformation(
    *,
    decision: str,
    transformation: GrantTransformation,
) -> bool:
    compatible = {
        GrantTransformation.RAW: frozenset({"allow"}),
        GrantTransformation.REDACTED: frozenset(
            {"allow", "allow_redacted"}
        ),
        GrantTransformation.SUMMARY: frozenset(
            {"allow", "allow_summary_only"}
        ),
    }
    return str(decision) in compatible[transformation]


def _grant_family_id(
    *,
    actor: GrantAdminActor,
    request: GrantCreateRequest,
    preset: GrantPreset,
    policy_version: str,
) -> str:
    return "grfam_" + _digest(
        {
            "tenant_id": actor.tenant_id,
            "project_id": actor.project_id,
            "source_revision_id": request.source_revision_id,
            "destination_id": request.destination_id,
            "operation": preset.operation.value,
            "transformation": preset.transformation.value,
            "purpose": preset.purpose,
            "policy_version": policy_version,
        }
    )


def _grant_etag(row: SourceAccessGrantDB) -> str:
    return _digest(
        {
            "grant_id": row.grant_id,
            "lock_version": row.lock_version,
            "state": row.state,
            "updated_at_epoch": row.updated_at_epoch,
        }
    )


def _audit_row(
    *,
    row: SourceAccessGrantDB,
    actor: GrantAdminActor,
    action: str,
    from_state: str | None,
    to_state: str,
    reason_code: str,
    lock_version: int,
    occurred_at: float,
) -> SourceAccessGrantAuditDB:
    audit_id = "audit_" + _digest(
        {
            "grant_id": row.grant_id,
            "action": action,
            "lock_version": lock_version,
            "actor_id": actor.subject_id,
            "occurred_at_epoch": occurred_at,
        }
    )
    return SourceAccessGrantAuditDB(
        audit_id=audit_id,
        grant_id=row.grant_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        owner_id=actor.subject_id,
        action=action,
        from_state=from_state,
        to_state=to_state,
        reason_code=reason_code,
        grant_lock_version=lock_version,
        occurred_at_epoch=occurred_at,
    )


def _operation_row(
    *,
    operation_key: str,
    request_digest: str,
    operation: str,
    result: GrantView,
    occurred_at: float,
) -> SourceControlOperationDB:
    return SourceControlOperationDB(
        idempotency_key=operation_key,
        request_digest=request_digest,
        operation=operation,
        state="completed",
        result_json=json.dumps(
            result.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        created_at_epoch=occurred_at,
        updated_at_epoch=occurred_at,
    )


def _digest(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _iso(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _encode_cursor(grant_id: str) -> str:
    return (
        base64.urlsafe_b64encode(grant_id.encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )


def _decode_cursor(cursor: str | None) -> str | None:
    if cursor in (None, ""):
        return None
    try:
        raw = str(cursor)
        raw += "=" * (-len(raw) % 4)
        grant_id = base64.urlsafe_b64decode(raw).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlGrantAdminError(
            "grant_cursor_invalid", status_code=400
        ) from exc
    if not _GRANT_ID.fullmatch(grant_id):
        raise SourceControlGrantAdminError(
            "grant_cursor_invalid", status_code=400
        )
    return grant_id


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
