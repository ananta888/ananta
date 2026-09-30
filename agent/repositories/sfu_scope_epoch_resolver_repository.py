"""SQL resolver and authorizer for SFU scope epoch grants."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_hub_control import (
    SfuScopeEpochAuthorityDB,
    SfuScopeEpochGrantDB,
)
from agent.models.sfu_browser_capability import SfuCapabilityAdmissionScope
from agent.models.sfu_layer_projection import SfuProjectionScope
from agent.repositories.sfu_hub_control_validation import (
    _positive_clock_ms,
    _safe_ref,
    SfuHubControlRepositoryError,
)


class SqlSfuScopeEpochResolver:
    """Read-only authority adapter; a missing row always denies the scope."""

    def __init__(
        self,
        *,
        identity_digest_secret: bytes,
        db_engine=default_engine,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if len(identity_digest_secret) < 32:
            raise ValueError("sfu_scope_epoch_secret_invalid")
        self._secret = bytes(identity_digest_secret)
        self._engine = db_engine
        self._clock_ms = clock_ms

    def resolve(
        self, *, tenant_id: str, room_id: str, actor_id: str
    ) -> SfuCapabilityAdmissionScope | None:
        if not all(_safe_ref(value) for value in (tenant_id, room_id, actor_id)):
            return None
        now_ms = _positive_clock_ms(self._clock_ms())
        try:
            with Session(self._engine) as db:
                row = self._active_scope(
                    db, tenant_id, room_id, actor_id, now_ms
                )
                if row is None:
                    return None
                return SfuCapabilityAdmissionScope(
                    tenant_id,
                    room_id,
                    actor_id,
                    row.admission_epoch,
                    row.membership_epoch,
                )
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_scope_epoch_store_unavailable"
            ) from exc

    def authorize(
        self,
        *,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        projection_kind: str,
        subject_ref: str,
    ) -> SfuProjectionScope | None:
        if (
            projection_kind not in {"room", "publisher", "receiver"}
            or not all(
                _safe_ref(value)
                for value in (
                    tenant_id,
                    room_id,
                    actor_id,
                    subject_ref,
                )
            )
        ):
            return None
        now_ms = _positive_clock_ms(self._clock_ms())
        try:
            with Session(self._engine) as db:
                scope = self._active_scope(
                    db, tenant_id, room_id, actor_id, now_ms
                )
                if scope is None:
                    return None
                if projection_kind == "room":
                    if subject_ref != room_id:
                        return None
                else:
                    if min(
                        scope.route_epoch,
                        scope.topology_epoch,
                        scope.key_epoch,
                    ) <= 0:
                        return None
                    grant = db.exec(
                        select(SfuScopeEpochGrantDB).where(
                            SfuScopeEpochGrantDB.tenant_id == tenant_id,
                            SfuScopeEpochGrantDB.room_id == room_id,
                            SfuScopeEpochGrantDB.actor_digest
                            == sfu_scope_identity_digest(
                                self._secret, "actor", actor_id
                            ),
                            SfuScopeEpochGrantDB.projection_kind
                            == projection_kind,
                            SfuScopeEpochGrantDB.subject_digest
                            == sfu_scope_identity_digest(
                                self._secret, "subject", subject_ref
                            ),
                            SfuScopeEpochGrantDB.status == "active",
                            SfuScopeEpochGrantDB.expires_at_ms > now_ms,
                            SfuScopeEpochGrantDB.scope_version
                            == scope.version,
                            SfuScopeEpochGrantDB.membership_epoch
                            == scope.membership_epoch,
                            SfuScopeEpochGrantDB.fencing_token
                            == scope.fencing_token,
                        )
                    ).first()
                    if grant is None:
                        return None
                return SfuProjectionScope(
                    tenant_id=tenant_id,
                    room_id=room_id,
                    actor_id=actor_id,
                    membership_epoch=scope.membership_epoch,
                    route_epoch=scope.route_epoch,
                    topology_epoch=scope.topology_epoch,
                    key_epoch=scope.key_epoch,
                )
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_scope_epoch_store_unavailable"
            ) from exc

    def _active_scope(
        self,
        db: Session,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        now_ms: int,
    ) -> SfuScopeEpochAuthorityDB | None:
        return db.exec(
            select(SfuScopeEpochAuthorityDB).where(
                SfuScopeEpochAuthorityDB.tenant_id == tenant_id,
                SfuScopeEpochAuthorityDB.room_id == room_id,
                SfuScopeEpochAuthorityDB.actor_digest
                == sfu_scope_identity_digest(
                    self._secret, "actor", actor_id
                ),
                SfuScopeEpochAuthorityDB.status == "active",
                SfuScopeEpochAuthorityDB.expires_at_ms > now_ms,
            )
        ).first()


def sfu_scope_identity_digest(
    secret: bytes, domain: str, value: str
) -> str:
    if (
        len(secret) < 32
        or domain not in {"actor", "subject"}
        or not _safe_ref(value)
    ):
        raise ValueError("sfu_scope_epoch_digest_input_invalid")
    return hmac.new(
        bytes(secret),
        f"ananta:sfu-scope-{domain}:v1\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


__all__ = [
    "SqlSfuScopeEpochResolver",
    "sfu_scope_identity_digest",
]
