"""Production SQL repository for Hub-owned spreadsheet learning state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.db_models.spreadsheet_studio import (
    SpreadsheetConsentRevocationImpactDB,
    SpreadsheetDatasetDB,
    SpreadsheetFeedbackEventDB,
    SpreadsheetTrainingConsentDB,
    SpreadsheetTrainingLineageDB,
)
from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json, require_digest, require_id


class SqlSpreadsheetLearningRepository:
    """Stores immutable manifests centrally and enforces tenant keys on every query."""

    durable = True
    production_component = True

    def __init__(self, *, db_engine) -> None:
        self._engine = db_engine

    def append_feedback(self, tenant_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(event)
        record = SpreadsheetFeedbackEventDB(
            tenant_id=require_id(tenant_id, "tenant_id"),
            event_id=require_id(value.get("event_id"), "event_id"),
            owner_id=require_id(value.get("owner_id"), "owner_id"),
            document_id=require_id(value.get("document_id"), "document_id"),
            record_digest=require_digest(value.get("record_digest"), "record_digest"),
            payload_json=canonical_json(value),
        )
        return self._insert_immutable(
            record,
            model=SpreadsheetFeedbackEventDB,
            key=(record.tenant_id, record.event_id),
            value=value,
            digest_field="digest",
            conflict_reason="spreadsheet_feedback_replay_conflict",
        )

    def get_feedback(self, tenant_id: str, event_id: str) -> dict[str, Any]:
        return self._get(
            SpreadsheetFeedbackEventDB,
            (require_id(tenant_id, "tenant_id"), require_id(event_id, "event_id")),
            "spreadsheet_feedback_not_found",
        )

    def append_consent(self, tenant_id: str, consent: Mapping[str, Any]) -> dict[str, Any]:
        with Session(bind=self._engine) as session, session.begin():
            return self._append_consent(session, tenant_id, consent)

    def append_consent_with_impact(
        self,
        tenant_id: str,
        consent: Mapping[str, Any],
        impact: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Persist the revocation revision and its fencing intent in one transaction."""

        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session, session.begin():
            persisted_consent = self._append_consent(session, tenant, consent)
            persisted_impact = self._append_impact(session, tenant, impact)
        return persisted_consent, persisted_impact

    def _append_consent(self, session: Session, tenant_id: str, consent: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        value = dict(consent)
        self._validate_digest(value, "consent_digest", "spreadsheet_consent_integrity_failed")
        consent_id = require_id(value.get("consent_id"), "consent_id")
        version = value.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("spreadsheet_consent_version_invalid")
        current = session.execute(
            select(SpreadsheetTrainingConsentDB)
            .where(
                SpreadsheetTrainingConsentDB.tenant_id == tenant,
                SpreadsheetTrainingConsentDB.consent_id == consent_id,
            )
            .order_by(SpreadsheetTrainingConsentDB.version.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if current is not None:
            previous = self._payload(current)
            if current.version >= version:
                if previous.get("consent_digest") == value.get("consent_digest"):
                    return {**previous, "replayed": True}
                raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
            if current.version + 1 != version:
                raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
        elif version != 1:
            raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
        session.add(
            SpreadsheetTrainingConsentDB(
                tenant_id=tenant,
                consent_id=consent_id,
                version=version,
                feedback_id=require_id(value.get("feedback_id"), "feedback_id"),
                owner_id=require_id(value.get("owner_id"), "owner_id"),
                state=str(value.get("state") or ""),
                consent_digest=require_digest(value.get("consent_digest"), "consent_digest"),
                payload_json=canonical_json(value),
            )
        )
        try:
            session.flush()
        except IntegrityError as exc:
            raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict") from exc
        return {**value, "replayed": False}

    def get_consent(self, tenant_id: str, consent_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        identity = require_id(consent_id, "consent_id")
        with Session(bind=self._engine) as session:
            record = session.execute(
                select(SpreadsheetTrainingConsentDB)
                .where(
                    SpreadsheetTrainingConsentDB.tenant_id == tenant,
                    SpreadsheetTrainingConsentDB.consent_id == identity,
                )
                .order_by(SpreadsheetTrainingConsentDB.version.desc())
                .limit(1)
            ).scalar_one_or_none()
        if record is None:
            raise KeyError("spreadsheet_consent_not_found")
        return self._payload(record)

    def get_active_consent_for_feedback(self, tenant_id: str, feedback_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        feedback = require_id(feedback_id, "feedback_id")
        with Session(bind=self._engine) as session:
            record = session.execute(
                select(SpreadsheetTrainingConsentDB)
                .where(
                    SpreadsheetTrainingConsentDB.tenant_id == tenant,
                    SpreadsheetTrainingConsentDB.feedback_id == feedback,
                )
                .order_by(SpreadsheetTrainingConsentDB.version.desc())
                .limit(1)
            ).scalar_one_or_none()
        if record is None:
            raise KeyError("spreadsheet_consent_not_found")
        value = self._payload(record)
        if value.get("state") != "active":
            raise PermissionError("spreadsheet_consent_inactive")
        return value

    def append_dataset(self, tenant_id: str, dataset: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(dataset)
        self._validate_digest(value, "digest", "spreadsheet_dataset_integrity_failed")
        split_lock = dict(value.get("split_lock") or {})
        self._validate_digest(split_lock, "split_lock_digest", "spreadsheet_split_lock_integrity_failed")
        record = SpreadsheetDatasetDB(
            tenant_id=require_id(tenant_id, "tenant_id"),
            dataset_id=require_id(value.get("dataset_id"), "dataset_id"),
            owner_id=require_id(value.get("owner_id"), "owner_id"),
            dataset_digest=require_digest(value.get("dataset_digest"), "dataset_digest"),
            split_lock_digest=require_digest(split_lock.get("split_lock_digest"), "split_lock_digest"),
            payload_json=canonical_json(value),
        )
        return self._insert_immutable(
            record,
            model=SpreadsheetDatasetDB,
            key=(record.tenant_id, record.dataset_id),
            value=value,
            digest_field="digest",
            conflict_reason="spreadsheet_dataset_replay_conflict",
        )

    def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any]:
        return self._get(
            SpreadsheetDatasetDB,
            (require_id(tenant_id, "tenant_id"), require_id(dataset_id, "dataset_id")),
            "spreadsheet_dataset_not_found",
        )

    def list_datasets(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session:
            records = list(
                session.execute(
                    select(SpreadsheetDatasetDB)
                    .where(SpreadsheetDatasetDB.tenant_id == tenant)
                    .order_by(SpreadsheetDatasetDB.dataset_id)
                ).scalars()
            )
        return [self._payload(record) for record in records]

    def append_training_lineage(self, tenant_id: str, lineage: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(lineage)
        record = SpreadsheetTrainingLineageDB(
            tenant_id=require_id(tenant_id, "tenant_id"),
            job_id=require_id(value.get("job_id"), "job_id"),
            dataset_id=require_id(value.get("dataset_id"), "dataset_id"),
            owner_id=require_id(value.get("owner_id"), "owner_id"),
            payload_json=canonical_json(value),
        )
        return self._insert_immutable(
            record,
            model=SpreadsheetTrainingLineageDB,
            key=(record.tenant_id, record.job_id),
            value=value,
            digest_field="digest",
            conflict_reason="spreadsheet_training_lineage_replay_conflict",
        )

    def list_training_lineage(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session:
            records = list(
                session.execute(
                    select(SpreadsheetTrainingLineageDB)
                    .where(SpreadsheetTrainingLineageDB.tenant_id == tenant)
                    .order_by(SpreadsheetTrainingLineageDB.job_id)
                ).scalars()
            )
        return [self._payload(record) for record in records]

    def append_revocation_impact(self, tenant_id: str, impact: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session, session.begin():
            return self._append_impact(session, tenant, impact)

    def _append_impact(self, session: Session, tenant_id: str, impact: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(impact)
        self._validate_digest(value, "digest", "spreadsheet_revocation_impact_integrity_failed")
        record = SpreadsheetConsentRevocationImpactDB(
            tenant_id=require_id(tenant_id, "tenant_id"),
            impact_id=require_id(value.get("impact_id"), "impact_id"),
            consent_id=require_id(value.get("consent_id"), "consent_id"),
            payload_json=canonical_json(value),
        )
        return self._insert_immutable_session(
            session,
            record,
            model=SpreadsheetConsentRevocationImpactDB,
            key=(record.tenant_id, record.impact_id),
            value=value,
            digest_field="digest",
            conflict_reason="spreadsheet_revocation_impact_replay_conflict",
        )

    def _insert_immutable(
        self,
        record: Any,
        *,
        model: Any,
        key: tuple[str, str],
        value: Mapping[str, Any],
        digest_field: str,
        conflict_reason: str,
    ) -> dict[str, Any]:
        self._validate_digest(value, digest_field, f"{conflict_reason}_integrity_failed")
        with Session(bind=self._engine) as session, session.begin():
            return self._insert_immutable_session(
                session,
                record,
                model=model,
                key=key,
                value=value,
                digest_field=digest_field,
                conflict_reason=conflict_reason,
            )

    @staticmethod
    def _insert_immutable_session(
        session: Session,
        record: Any,
        *,
        model: Any,
        key: tuple[str, str],
        value: Mapping[str, Any],
        digest_field: str,
        conflict_reason: str,
    ) -> dict[str, Any]:
        existing = session.get(model, key)
        if existing is not None:
            previous = json.loads(existing.payload_json)
            SqlSpreadsheetLearningRepository._validate_digest(
                previous,
                digest_field,
                "spreadsheet_learning_payload_integrity_failed",
            )
            if previous.get(digest_field) == value.get(digest_field):
                return {**previous, "replayed": True}
            raise SpreadsheetLearningConflict(conflict_reason)
        session.add(record)
        try:
            session.flush()
        except IntegrityError as exc:
            raise SpreadsheetLearningConflict(conflict_reason) from exc
        return {**dict(value), "replayed": False}

    def _get(self, model: Any, key: tuple[str, str], missing: str) -> dict[str, Any]:
        with Session(bind=self._engine) as session:
            record = session.get(model, key)
        if record is None:
            raise KeyError(missing)
        return self._payload(record)

    @staticmethod
    def _payload(record: Any) -> dict[str, Any]:
        value = dict(json.loads(record.payload_json))
        digest_field = "consent_digest" if isinstance(record, SpreadsheetTrainingConsentDB) else "digest"
        SqlSpreadsheetLearningRepository._validate_digest(
            value,
            digest_field,
            "spreadsheet_learning_payload_integrity_failed",
        )
        if isinstance(record, SpreadsheetDatasetDB):
            SqlSpreadsheetLearningRepository._validate_digest(
                dict(value.get("split_lock") or {}),
                "split_lock_digest",
                "spreadsheet_split_lock_integrity_failed",
            )
        return value

    @staticmethod
    def _validate_digest(value: Mapping[str, Any], field: str, reason: str) -> None:
        unsigned = dict(value)
        supplied = require_digest(unsigned.pop(field, None), field)
        if canonical_digest(unsigned) != supplied:
            raise RuntimeError(reason)


__all__ = ["SqlSpreadsheetLearningRepository"]
