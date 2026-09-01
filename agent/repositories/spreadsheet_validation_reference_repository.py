"""SQL repository for immutable tenant-scoped validation references."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.db_models.spreadsheet_studio import SpreadsheetValidationReferenceDB
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json, require_id


class SqlSpreadsheetValidationReferenceRepository:
    durable = True
    production_component = True

    def __init__(self, *, db_engine) -> None:
        self._engine = db_engine

    def create_reference(self, tenant_id: str, reference: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        value = dict(reference)
        record = SpreadsheetValidationReferenceDB(
            tenant_id=tenant,
            reference_id=require_id(value.get("reference_id"), "reference_id"),
            document_id=require_id(value.get("document_id"), "document_id"),
            document_version=int(value["document_version"]),
            snapshot_digest=str(value["snapshot_digest"]),
            reference_digest=str(value["reference_digest"]),
            payload_json=canonical_json(value),
        )
        try:
            with Session(bind=self._engine) as session, session.begin():
                session.add(record)
        except IntegrityError as exc:
            raise SpreadsheetStoreConflict("spreadsheet_validation_reference_exists") from exc
        return value

    def get_reference(self, tenant_id: str, reference_id: str) -> dict[str, Any]:
        key = (require_id(tenant_id, "tenant_id"), require_id(reference_id, "reference_id"))
        with Session(bind=self._engine) as session:
            record = session.get(SpreadsheetValidationReferenceDB, key)
        if record is None:
            raise KeyError("spreadsheet_validation_reference_not_found")
        return self._payload(record)

    def list_references(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("spreadsheet_validation_reference_limit_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session:
            records = list(
                session.execute(
                    select(SpreadsheetValidationReferenceDB)
                    .where(SpreadsheetValidationReferenceDB.tenant_id == tenant)
                    .order_by(SpreadsheetValidationReferenceDB.reference_id)
                    .limit(limit)
                ).scalars()
            )
        return {"items": [self._payload(record) for record in records], "limit": limit}

    @staticmethod
    def _payload(record: SpreadsheetValidationReferenceDB) -> dict[str, Any]:
        value = json.loads(record.payload_json)
        supplied = str(value.pop("reference_digest", ""))
        if supplied != record.reference_digest or canonical_digest(value) != supplied:
            raise RuntimeError("spreadsheet_validation_reference_integrity_failed")
        if value.get("tenant_digest") != canonical_digest({"tenant_id": record.tenant_id}):
            raise RuntimeError("spreadsheet_validation_reference_tenant_integrity_failed")
        if value.get("snapshot_digest") != record.snapshot_digest:
            raise RuntimeError("spreadsheet_validation_reference_snapshot_integrity_failed")
        return {**value, "reference_digest": supplied}


__all__ = ["SqlSpreadsheetValidationReferenceRepository"]
