"""Persistent one-time approvals for destructive source-control operations."""

from __future__ import annotations

import re
import secrets
import time

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session

from agent.db_models.source_control import SourceControlPurgeApprovalDB
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleError,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SQLSourceControlPurgeApprovalStore:
    """CAS adapter for approval issuance, lease/reclaim and consumption."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock=time.time,
        claim_lease_seconds: float = 300.0,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("purge_approval_lease_invalid")
        self._engine = engine
        self._clock = clock
        self._claim_lease_seconds = float(claim_lease_seconds)

    def issue(
        self,
        *,
        tenant_id: str,
        project_id: str,
        action: str,
        object_type: str,
        object_id: str,
        request_digest: str,
        approved_by: str,
        expires_at_epoch: float,
        approval_id: str | None = None,
    ) -> str:
        for value in (
            tenant_id,
            project_id,
            action,
            object_type,
            object_id,
            approved_by,
        ):
            if not _OPAQUE_ID.fullmatch(str(value or "")):
                raise SourceIndexLifecycleError("purge_approval_scope_invalid")
        if not _SHA256.fullmatch(str(request_digest or "")):
            raise SourceIndexLifecycleError("purge_approval_digest_invalid")
        now = float(self._clock())
        if float(expires_at_epoch) <= now:
            raise SourceIndexLifecycleError("purge_approval_expired")
        identifier = approval_id or f"purge_{secrets.token_hex(24)}"
        if not _OPAQUE_ID.fullmatch(identifier):
            raise SourceIndexLifecycleError("purge_approval_id_invalid")
        with Session(self._engine) as db:
            existing = db.get(SourceControlPurgeApprovalDB, identifier)
            coordinates = (
                tenant_id,
                project_id,
                action,
                object_type,
                object_id,
                request_digest,
                approved_by,
                float(expires_at_epoch),
            )
            if existing is not None:
                stored = (
                    existing.tenant_id,
                    existing.project_id,
                    existing.action,
                    existing.object_type,
                    existing.object_id,
                    existing.request_digest,
                    existing.approved_by,
                    float(existing.expires_at_epoch),
                )
                if stored != coordinates:
                    raise SourceIndexLifecycleError(
                        "purge_approval_id_conflict"
                    )
                return identifier
            db.add(
                SourceControlPurgeApprovalDB(
                    approval_id=identifier,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    action=action,
                    object_type=object_type,
                    object_id=object_id,
                    request_digest=request_digest,
                    approved_by=approved_by,
                    state="approved",
                    issued_at_epoch=now,
                    expires_at_epoch=float(expires_at_epoch),
                    lock_version=1,
                )
            )
            db.commit()
        return identifier

    def claim(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        project_id: str,
        action: str,
        object_type: str,
        object_id: str,
        request_digest: str,
        claim_id: str,
    ) -> None:
        now = float(self._clock())
        with Session(self._engine) as db:
            row = db.get(SourceControlPurgeApprovalDB, approval_id)
            if row is None:
                raise SourceIndexLifecycleError("purge_approval_not_found")
            self._require_binding(
                row=row,
                tenant_id=tenant_id,
                project_id=project_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                request_digest=request_digest,
                now=now,
            )
            if row.state == "consumed":
                if row.claim_id == claim_id:
                    return
                raise SourceIndexLifecycleError(
                    "purge_approval_already_consumed"
                )
            if (
                row.state == "claimed"
                and row.claim_id == claim_id
                and float(row.claim_expires_at_epoch or 0) > now
            ):
                return
            reclaimable = row.state == "approved" or (
                row.state == "claimed"
                and float(row.claim_expires_at_epoch or 0) <= now
            )
            if not reclaimable:
                raise SourceIndexLifecycleError(
                    "purge_approval_in_progress"
                )
            mutation = db.exec(
                update(SourceControlPurgeApprovalDB)
                .where(
                    SourceControlPurgeApprovalDB.approval_id == approval_id,
                    SourceControlPurgeApprovalDB.lock_version
                    == row.lock_version,
                    SourceControlPurgeApprovalDB.state == row.state,
                )
                .values(
                    state="claimed",
                    claim_id=claim_id,
                    claim_expires_at_epoch=now
                    + self._claim_lease_seconds,
                    lock_version=row.lock_version + 1,
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceIndexLifecycleError(
                    "purge_approval_claim_conflict"
                )
            db.commit()

    def consume(
        self,
        *,
        approval_id: str,
        request_digest: str,
        claim_id: str,
    ) -> None:
        now = float(self._clock())
        with Session(self._engine) as db:
            row = db.get(SourceControlPurgeApprovalDB, approval_id)
            if row is None or row.request_digest != request_digest:
                raise SourceIndexLifecycleError(
                    "purge_approval_binding_mismatch"
                )
            if row.state == "consumed" and row.claim_id == claim_id:
                return
            mutation = db.exec(
                update(SourceControlPurgeApprovalDB)
                .where(
                    SourceControlPurgeApprovalDB.approval_id == approval_id,
                    SourceControlPurgeApprovalDB.request_digest
                    == request_digest,
                    SourceControlPurgeApprovalDB.state == "claimed",
                    SourceControlPurgeApprovalDB.claim_id == claim_id,
                    SourceControlPurgeApprovalDB.claim_expires_at_epoch > now,
                    SourceControlPurgeApprovalDB.lock_version
                    == row.lock_version,
                )
                .values(
                    state="consumed",
                    consumed_at_epoch=now,
                    lock_version=row.lock_version + 1,
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceIndexLifecycleError(
                    "purge_approval_consume_conflict"
                )
            db.commit()

    @staticmethod
    def _require_binding(
        *,
        row: SourceControlPurgeApprovalDB,
        tenant_id: str,
        project_id: str,
        action: str,
        object_type: str,
        object_id: str,
        request_digest: str,
        now: float,
    ) -> None:
        if float(row.expires_at_epoch) <= now:
            raise SourceIndexLifecycleError("purge_approval_expired")
        if (
            row.tenant_id != tenant_id
            or row.project_id != project_id
            or row.action != action
            or row.object_type != object_type
            or row.object_id != object_id
            or row.request_digest != request_digest
        ):
            raise SourceIndexLifecycleError(
                "purge_approval_binding_mismatch"
            )


__all__ = ["SQLSourceControlPurgeApprovalStore"]
