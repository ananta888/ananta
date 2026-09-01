"""Durable local reference store used by the bounded mock composition."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json, require_id


class SpreadsheetValidationReferenceStore:
    durable = True

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def create_reference(self, tenant_id: str, reference: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        reference_id = require_id(reference.get("reference_id"), "reference_id")
        value = dict(reference)
        try:
            with self._transaction, self._connect() as connection:
                connection.execute(
                    "INSERT INTO spreadsheet_validation_references"
                    "(tenant_id,reference_id,reference_digest,payload_json) VALUES(?,?,?,?)",
                    (tenant, reference_id, str(value["reference_digest"]), canonical_json(value)),
                )
        except sqlite3.IntegrityError as exc:
            raise SpreadsheetStoreConflict("spreadsheet_validation_reference_exists") from exc
        return value

    def get_reference(self, tenant_id: str, reference_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        reference = require_id(reference_id, "reference_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT reference_digest,payload_json FROM spreadsheet_validation_references "
                "WHERE tenant_id=? AND reference_id=?",
                (tenant, reference),
            ).fetchone()
        if row is None:
            raise KeyError("spreadsheet_validation_reference_not_found")
        return self._verified(row, tenant)

    def list_references(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("spreadsheet_validation_reference_limit_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reference_digest,payload_json FROM spreadsheet_validation_references "
                "WHERE tenant_id=? ORDER BY reference_id LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [self._verified(row, tenant) for row in rows], "limit": limit}

    @staticmethod
    def _verified(row, tenant_id: str) -> dict[str, Any]:
        value = json.loads(row[1])
        supplied = str(value.pop("reference_digest", ""))
        if (
            supplied != row[0]
            or canonical_digest(value) != supplied
            or value.get("tenant_digest") != canonical_digest({"tenant_id": tenant_id})
        ):
            raise RuntimeError("spreadsheet_validation_reference_integrity_failed")
        return {**value, "reference_digest": supplied}

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS spreadsheet_validation_references("
                "tenant_id TEXT NOT NULL,reference_id TEXT NOT NULL,reference_digest TEXT NOT NULL,"
                "payload_json TEXT NOT NULL,PRIMARY KEY(tenant_id,reference_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["SpreadsheetValidationReferenceStore"]
