"""Persistent adapters behind the effective source access service.

Scoped source-revision and destination catalogs plus the grant-based
effective policy that only allows exact, active, unexpired grant coordinates
bound to the current Context Policy snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceRevisionDB,
)
from agent.services.effective_source_access_service import (
    EffectivePolicyEvaluation,
    EffectiveSourceAccessService,
    EffectiveSourceRevision,
)
from agent.services.source_control_adapter_common import (
    SourceControlProductionAdapterError,
)
from agent.services.source_control_destination_catalog import (
    ScopedWorkerModelDestinationCatalog,
)
from ananta_contracts.source_control import DestinationDescriptor


class SQLSourceRevisionAccessCatalog:
    """Tenant/project-scoped source revision projection for access evaluation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> EffectiveSourceRevision | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            return None if row is None else self._record(row)

    def list_revisions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[EffectiveSourceRevision], str | None]:
        after = self._decode_cursor(cursor)
        with Session(self._engine) as db:
            statement = select(SourceRevisionDB).where(
                SourceRevisionDB.tenant_id == tenant_id,
                SourceRevisionDB.project_id == project_id,
            )
            if after is not None:
                statement = statement.where(
                    SourceRevisionDB.source_revision_id > after
                )
            if filters.get("source_type"):
                statement = statement.where(
                    SourceRevisionDB.connector_type
                    == filters["source_type"]
                )
            if filters.get("sensitivity"):
                statement = statement.where(
                    SourceRevisionDB.sensitivity
                    == filters["sensitivity"]
                )
            rows = list(
                db.exec(
                    statement.order_by(
                        SourceRevisionDB.source_revision_id
                    ).limit(limit + 1)
                ).all()
            )
        visible = rows[:limit]
        return (
            tuple(self._record(row) for row in visible),
            (
                base64.urlsafe_b64encode(
                    visible[-1].source_revision_id.encode("ascii")
                )
                .decode("ascii")
                .rstrip("=")
                if len(rows) > limit and visible
                else None
            ),
        )

    @staticmethod
    def _record(row: SourceRevisionDB) -> EffectiveSourceRevision:
        return EffectiveSourceRevision(
            source_revision_id=row.source_revision_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_type=row.connector_type,
            sensitivity=row.sensitivity,
            revision_digest=row.revision_digest,
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> str | None:
        if cursor is None:
            return None
        try:
            value = cursor + "=" * (-len(cursor) % 4)
            return base64.urlsafe_b64decode(value).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceControlProductionAdapterError(
                "source_revision_cursor_invalid"
            ) from exc


class ScopedEffectiveDestinationCatalog:
    def __init__(
        self,
        catalog: ScopedWorkerModelDestinationCatalog,
        *,
        tenant_id: str,
        project_id: str,
    ) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._project_id = project_id

    def get_destination(
        self, *, destination_id: str
    ) -> DestinationDescriptor | None:
        return self._catalog.get(
            tenant_id=self._tenant_id,
            project_id=self._project_id,
            destination_id=destination_id,
        )

    def list_destinations(
        self,
        *,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[DestinationDescriptor], str | None]:
        return self._catalog.list(
            tenant_id=self._tenant_id,
            project_id=self._project_id,
            cursor=cursor,
            limit=limit,
            filters=filters,
        )


class PersistentGrantEffectivePolicy:
    """Evaluate effective access from exact active grant coordinates."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def evaluate(
        self,
        *,
        source_revision: EffectiveSourceRevision,
        destination: DestinationDescriptor,
        operation: object,
        transformation: object,
        purpose: str,
    ) -> EffectivePolicyEvaluation:
        operation_value = str(getattr(operation, "value", operation))
        transformation_value = str(
            getattr(transformation, "value", transformation)
        )
        with Session(self._engine) as db:
            grants = list(
                db.exec(
                    select(SourceAccessGrantDB).where(
                        SourceAccessGrantDB.tenant_id
                        == source_revision.tenant_id,
                        SourceAccessGrantDB.project_id
                        == source_revision.project_id,
                        SourceAccessGrantDB.source_revision_id
                        == source_revision.source_revision_id,
                        SourceAccessGrantDB.destination_id
                        == destination.destination_id,
                        SourceAccessGrantDB.operation == operation_value,
                        SourceAccessGrantDB.transformation
                        == transformation_value,
                        SourceAccessGrantDB.purpose == purpose,
                        SourceAccessGrantDB.state == "active",
                        SourceAccessGrantDB.expires_at_epoch
                        > float(self._clock()),
                    )
                ).all()
            )
        grant = max(
            grants, key=lambda item: item.grant_version, default=None
        )
        if grant is None:
            return EffectivePolicyEvaluation(
                decision="deny",
                reason_codes=("active_grant_not_found",),
                matched_rule_path=(),
                default_applied=True,
                approval_requirement=None,
                policy_digest=hashlib.sha256(
                    b"ananta.source-control.no-active-grant.v1"
                ).hexdigest(),
            )
        policy_digest = self._resolve_policy_snapshot_digest(
            tenant_id=source_revision.tenant_id,
            project_id=source_revision.project_id,
            policy_snapshot_id=grant.policy_version,
            stored_digest=grant.policy_snapshot_digest,
        )
        return EffectivePolicyEvaluation(
            decision="allow",
            reason_codes=("active_grant",),
            matched_rule_path=(grant.grant_id,),
            default_applied=False,
            approval_requirement=None,
            policy_digest=policy_digest,
        )

    def _resolve_policy_snapshot_digest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_snapshot_id: str,
        stored_digest: str | None,
    ) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", str(stored_digest or "")):
            raise SourceControlProductionAdapterError(
                "policy_snapshot_digest_missing",
                status_code=409,
            )
        with Session(self._engine) as db:
            rows = db.exec(
                select(ContextPolicyVersionDB).where(
                    ContextPolicyVersionDB.tenant_id == tenant_id,
                    ContextPolicyVersionDB.project_id == project_id,
                    ContextPolicyVersionDB.state == "active",
                )
            ).all()
        matched = [
            row
            for row in rows
            if derive_policy_snapshot_id(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                policy_id=row.policy_id,
                version=int(row.version),
                policy_digest=row.policy_digest,
            )
            == policy_snapshot_id
        ]
        if len(matched) != 1 or matched[0].policy_digest != stored_digest:
            raise SourceControlProductionAdapterError(
                "policy_snapshot_binding_mismatch",
                status_code=409,
            )
        return str(stored_digest)


def derive_policy_snapshot_id(
    *,
    tenant_id: str,
    project_id: str,
    policy_id: str,
    version: int,
    policy_digest: str,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "policy_id": policy_id,
        "version": version,
        "policy_digest": policy_digest,
    }
    return "cpv_" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def build_scoped_effective_access_service(
    *,
    engine: Engine,
    destinations: ScopedWorkerModelDestinationCatalog,
    tenant_id: str,
    project_id: str,
) -> EffectiveSourceAccessService:
    return EffectiveSourceAccessService(
        sources=SQLSourceRevisionAccessCatalog(engine),
        destinations=ScopedEffectiveDestinationCatalog(
            destinations,
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        policy=PersistentGrantEffectivePolicy(engine),
    )


__all__ = [
    "PersistentGrantEffectivePolicy",
    "SQLSourceRevisionAccessCatalog",
    "ScopedEffectiveDestinationCatalog",
    "build_scoped_effective_access_service",
    "derive_policy_snapshot_id",
]
