"""SQLite persistence for immutable spreadsheet feedback, consent and datasets."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningConflict
from ananta_contracts.spreadsheet_studio import canonical_json, require_id


class SpreadsheetLearningStore:
    durable = True
    production_component = False

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".learning.lock"))
        self._initialize()

    def append_feedback(self, tenant_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        return self._insert_immutable(
            table="spreadsheet_feedback_events",
            tenant_id=tenant_id,
            identity_field="event_id",
            identity=str(event.get("event_id") or ""),
            payload=event,
            conflict_reason="spreadsheet_feedback_replay_conflict",
        )

    def get_feedback(self, tenant_id: str, event_id: str) -> dict[str, Any]:
        return self._get(
            "spreadsheet_feedback_events", tenant_id, "event_id", event_id, "spreadsheet_feedback_not_found"
        )

    def append_consent(self, tenant_id: str, consent: Mapping[str, Any]) -> dict[str, Any]:
        with self._transaction, self._connect() as connection:
            return self._append_consent(connection, tenant_id, consent)

    def append_consent_with_impact(
        self,
        tenant_id: str,
        consent: Mapping[str, Any],
        impact: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._transaction, self._connect() as connection:
            persisted_consent = self._append_consent(connection, tenant_id, consent)
            persisted_impact = self._insert_immutable_connection(
                connection,
                table="spreadsheet_revocation_impacts",
                tenant_id=tenant_id,
                identity_field="impact_id",
                identity=str(impact.get("impact_id") or ""),
                payload=impact,
                conflict_reason="spreadsheet_revocation_impact_replay_conflict",
            )
        return persisted_consent, persisted_impact

    def _append_consent(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        consent: Mapping[str, Any],
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        consent_id = require_id(consent.get("consent_id"), "consent_id")
        version = consent.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("spreadsheet_consent_version_invalid")
        payload = dict(consent)
        current = connection.execute(
            "SELECT version,payload_json FROM spreadsheet_consents WHERE tenant_id=? AND consent_id=? "
            "ORDER BY version DESC LIMIT 1",
            (tenant, consent_id),
        ).fetchone()
        if current:
            if int(current[0]) >= version:
                previous = json.loads(current[1])
                if previous.get("consent_digest") == payload.get("consent_digest"):
                    return {**previous, "replayed": True}
                raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
            if int(current[0]) + 1 != version:
                raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
        elif version != 1:
            raise SpreadsheetLearningConflict("spreadsheet_consent_version_conflict")
        connection.execute(
            "INSERT INTO spreadsheet_consents"
            "(tenant_id,consent_id,feedback_id,version,payload_json) VALUES(?,?,?,?,?)",
            (
                tenant,
                consent_id,
                require_id(consent.get("feedback_id"), "feedback_id"),
                version,
                canonical_json(payload),
            ),
        )
        return {**payload, "replayed": False}

    def get_consent(self, tenant_id: str, consent_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        identity = require_id(consent_id, "consent_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM spreadsheet_consents WHERE tenant_id=? AND consent_id=? "
                "ORDER BY version DESC LIMIT 1",
                (tenant, identity),
            ).fetchone()
        if not row:
            raise KeyError("spreadsheet_consent_not_found")
        return json.loads(row[0])

    def get_active_consent_for_feedback(self, tenant_id: str, feedback_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        feedback = require_id(feedback_id, "feedback_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM spreadsheet_consents WHERE tenant_id=? AND feedback_id=? "
                "ORDER BY version DESC LIMIT 1",
                (tenant, feedback),
            ).fetchone()
        if not row:
            raise KeyError("spreadsheet_consent_not_found")
        value = json.loads(row[0])
        if value.get("state") != "active":
            raise PermissionError("spreadsheet_consent_inactive")
        return value

    def append_dataset(self, tenant_id: str, dataset: Mapping[str, Any]) -> dict[str, Any]:
        return self._insert_immutable(
            table="spreadsheet_datasets",
            tenant_id=tenant_id,
            identity_field="dataset_id",
            identity=str(dataset.get("dataset_id") or ""),
            payload=dataset,
            conflict_reason="spreadsheet_dataset_replay_conflict",
        )

    def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any]:
        return self._get("spreadsheet_datasets", tenant_id, "dataset_id", dataset_id, "spreadsheet_dataset_not_found")

    def list_datasets(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM spreadsheet_datasets WHERE tenant_id=? ORDER BY dataset_id",
                (tenant,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_training_lineage(self, tenant_id: str, lineage: Mapping[str, Any]) -> dict[str, Any]:
        return self._insert_immutable(
            table="spreadsheet_training_lineage",
            tenant_id=tenant_id,
            identity_field="job_id",
            identity=str(lineage.get("job_id") or ""),
            payload=lineage,
            conflict_reason="spreadsheet_training_lineage_replay_conflict",
        )

    def list_training_lineage(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM spreadsheet_training_lineage WHERE tenant_id=? ORDER BY job_id",
                (tenant,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_revocation_impact(self, tenant_id: str, impact: Mapping[str, Any]) -> dict[str, Any]:
        return self._insert_immutable(
            table="spreadsheet_revocation_impacts",
            tenant_id=tenant_id,
            identity_field="impact_id",
            identity=str(impact.get("impact_id") or ""),
            payload=impact,
            conflict_reason="spreadsheet_revocation_impact_replay_conflict",
        )

    def _insert_immutable(
        self,
        *,
        table: str,
        tenant_id: str,
        identity_field: str,
        identity: str,
        payload: Mapping[str, Any],
        conflict_reason: str,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        normalized_identity = require_id(identity, identity_field)
        value = dict(payload)
        with self._transaction, self._connect() as connection:
            return self._insert_immutable_connection(
                connection,
                table=table,
                tenant_id=tenant,
                identity_field=identity_field,
                identity=normalized_identity,
                payload=value,
                conflict_reason=conflict_reason,
            )

    @staticmethod
    def _insert_immutable_connection(
        connection: sqlite3.Connection,
        *,
        table: str,
        tenant_id: str,
        identity_field: str,
        identity: str,
        payload: Mapping[str, Any],
        conflict_reason: str,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        normalized_identity = require_id(identity, identity_field)
        value = dict(payload)
        existing = connection.execute(
            f"SELECT payload_json FROM {table} WHERE tenant_id=? AND {identity_field}=?",  # noqa: S608
            (tenant, normalized_identity),
        ).fetchone()
        if existing:
            previous = json.loads(existing[0])
            if previous.get("digest") == value.get("digest"):
                return {**previous, "replayed": True}
            raise SpreadsheetLearningConflict(conflict_reason)
        connection.execute(
            f"INSERT INTO {table}(tenant_id,{identity_field},payload_json) VALUES(?,?,?)",  # noqa: S608
            (tenant, normalized_identity, canonical_json(value)),
        )
        return {**value, "replayed": False}

    def _get(self, table: str, tenant_id: str, field: str, identity: str, missing: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        normalized_identity = require_id(identity, field)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE tenant_id=? AND {field}=?",  # noqa: S608 - internal constants
                (tenant, normalized_identity),
            ).fetchone()
        if not row:
            raise KeyError(missing)
        return json.loads(row[0])

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS spreadsheet_feedback_events(
                    tenant_id TEXT NOT NULL,event_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,event_id));
                CREATE TABLE IF NOT EXISTS spreadsheet_consents(
                    tenant_id TEXT NOT NULL,consent_id TEXT NOT NULL,feedback_id TEXT NOT NULL,
                    version INTEGER NOT NULL,payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,consent_id,version));
                CREATE INDEX IF NOT EXISTS ix_spreadsheet_consents_feedback
                    ON spreadsheet_consents(tenant_id,feedback_id,version);
                CREATE TABLE IF NOT EXISTS spreadsheet_datasets(
                    tenant_id TEXT NOT NULL,dataset_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,dataset_id));
                CREATE TABLE IF NOT EXISTS spreadsheet_training_lineage(
                    tenant_id TEXT NOT NULL,job_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,job_id));
                CREATE TABLE IF NOT EXISTS spreadsheet_revocation_impacts(
                    tenant_id TEXT NOT NULL,impact_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,impact_id));
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


__all__ = ["SpreadsheetLearningConflict", "SpreadsheetLearningStore"]
