"""Append-only SQL adapter for controlling profiles and mapping receipts."""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.business_controlling import BusinessControllingMappingDB, BusinessControllingProfileDB
from agent.services.business_controlling_import_service import MappingConfirmation, TabularProfile


class BusinessControllingProfilePersistenceError(RuntimeError):
    pass


class SqlBusinessControllingProfileRepository:
    def __init__(self, engine: Engine, *, clock: Callable[[], float] = time.time) -> None:
        self._engine = engine
        self._clock = clock

    def append_profile(self, *, tenant_id: str, project_id: str, profile: TabularProfile) -> TabularProfile:
        payload = {
            "source_revision_id": profile.source_revision_id,
            "revision_digest": profile.revision_digest,
            "row_count": profile.row_count,
            "duplicate_row_count": profile.duplicate_row_count,
            # Keep the JSON projection stable across SQL adapter round-trips.
            # JSON backends deserialize tuples as lists, so persisting asdict()
            # directly would make an idempotent retry look like a conflict.
            "columns": [
                {
                    "header": column.header,
                    "inferred_type": column.inferred_type,
                    "null_count": column.null_count,
                    "invalid_count": column.invalid_count,
                    "invalid_locators": list(column.invalid_locators),
                }
                for column in profile.columns
            ],
            "profile_digest": profile.profile_digest,
        }
        row = BusinessControllingProfileDB(
            profile_digest=profile.profile_digest,
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=profile.source_revision_id,
            revision_digest=profile.revision_digest,
            payload=payload,
            created_at_epoch=float(self._clock()),
        )
        with Session(self._engine) as session:
            existing = session.get(BusinessControllingProfileDB, profile.profile_digest)
            if existing is not None:
                self._assert_profile_identity(existing, tenant_id, project_id, payload)
                return profile
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = session.exec(
                    select(BusinessControllingProfileDB).where(
                        BusinessControllingProfileDB.tenant_id == tenant_id,
                        BusinessControllingProfileDB.project_id == project_id,
                        BusinessControllingProfileDB.source_revision_id == profile.source_revision_id,
                        BusinessControllingProfileDB.revision_digest == profile.revision_digest,
                    )
                ).first()
                if concurrent is None:
                    raise BusinessControllingProfilePersistenceError(
                        "controlling_profile_identity_conflict"
                    ) from None
                self._assert_profile_identity(concurrent, tenant_id, project_id, payload)
        return profile

    def append_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        confirmation: MappingConfirmation,
    ) -> MappingConfirmation:
        mapping = dict(confirmation.column_mapping)
        with Session(self._engine) as session:
            profile = session.exec(
                select(BusinessControllingProfileDB).where(
                    BusinessControllingProfileDB.profile_digest == confirmation.profile_digest,
                    BusinessControllingProfileDB.tenant_id == tenant_id,
                    BusinessControllingProfileDB.project_id == project_id,
                )
            ).first()
            if profile is None:
                raise BusinessControllingProfilePersistenceError("controlling_profile_not_found")
            existing = session.exec(
                select(BusinessControllingMappingDB).where(
                    BusinessControllingMappingDB.profile_digest == confirmation.profile_digest
                )
            ).first()
            if existing is not None:
                self._assert_mapping_identity(existing, tenant_id, project_id, confirmation, mapping)
                return confirmation
            session.add(
                BusinessControllingMappingDB(
                    confirmation_digest=confirmation.confirmation_digest,
                    profile_digest=confirmation.profile_digest,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    column_mapping=mapping,
                    confirmed_by=confirmation.confirmed_by,
                    created_at_epoch=float(self._clock()),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = session.exec(
                    select(BusinessControllingMappingDB).where(
                        BusinessControllingMappingDB.profile_digest == confirmation.profile_digest
                    )
                ).first()
                if concurrent is None:
                    raise BusinessControllingProfilePersistenceError(
                        "controlling_mapping_identity_conflict"
                    ) from None
                self._assert_mapping_identity(concurrent, tenant_id, project_id, confirmation, mapping)
        return confirmation

    @staticmethod
    def _assert_profile_identity(
        row: BusinessControllingProfileDB,
        tenant_id: str,
        project_id: str,
        payload: dict[str, object],
    ) -> None:
        if row.tenant_id != tenant_id or row.project_id != project_id or row.payload != payload:
            raise BusinessControllingProfilePersistenceError("controlling_profile_identity_conflict")

    @staticmethod
    def _assert_mapping_identity(
        row: BusinessControllingMappingDB,
        tenant_id: str,
        project_id: str,
        confirmation: MappingConfirmation,
        mapping: dict[str, str],
    ) -> None:
        if (
            row.tenant_id != tenant_id
            or row.project_id != project_id
            or row.confirmation_digest != confirmation.confirmation_digest
            or row.column_mapping != mapping
            or row.confirmed_by != confirmation.confirmed_by
        ):
            raise BusinessControllingProfilePersistenceError("controlling_mapping_identity_conflict")


__all__ = ["BusinessControllingProfilePersistenceError", "SqlBusinessControllingProfileRepository"]
