"""Immutable tenant-scoped spreadsheet versions and proposal results."""

# Keep complete SQLite key declarations on one physical line for schema audits.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.ports.spreadsheet import SpreadsheetStoreConflict
from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.spreadsheet_studio import canonical_json, require_id


class SpreadsheetStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def create_document(self, tenant_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        document_id = require_id(document.get("document_id"), "document_id")
        value = {**dict(document), "tenant_id": tenant, "version": 1}
        with self._transaction, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO spreadsheet_documents(tenant_id,document_id,current_version) VALUES(?,?,?)",
                    (tenant, document_id, 1),
                )
                connection.execute(
                    "INSERT INTO spreadsheet_versions(tenant_id,document_id,version,payload_json) VALUES(?,?,?,?)",
                    (tenant, document_id, 1, canonical_json(value)),
                )
            except sqlite3.IntegrityError as exc:
                raise SpreadsheetStoreConflict("spreadsheet_document_exists") from exc
        return value

    def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        document = require_id(document_id, "document_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT v.payload_json FROM spreadsheet_documents d JOIN spreadsheet_versions v "
                "ON v.tenant_id=d.tenant_id AND v.document_id=d.document_id AND v.version=d.current_version "
                "WHERE d.tenant_id=? AND d.document_id=?",
                (tenant, document),
            ).fetchone()
        if not row:
            raise KeyError("spreadsheet_document_not_found")
        return json.loads(row[0])

    def get_version(self, tenant_id: str, document_id: str, version: int) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        document = require_id(document_id, "document_id")
        number = self._version_number(version)
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM spreadsheet_documents WHERE tenant_id=? AND document_id=?",
                (tenant, document),
            ).fetchone()
            row = connection.execute(
                "SELECT payload_json FROM spreadsheet_versions WHERE tenant_id=? AND document_id=? AND version=?",
                (tenant, document, number),
            ).fetchone()
        if not exists:
            raise KeyError("spreadsheet_document_not_found")
        if not row:
            raise KeyError("spreadsheet_document_version_not_found")
        return json.loads(row[0])

    def list_documents(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("spreadsheet_document_list_limit_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT v.payload_json FROM spreadsheet_documents d JOIN spreadsheet_versions v "
                "ON v.tenant_id=d.tenant_id AND v.document_id=d.document_id AND v.version=d.current_version "
                "WHERE d.tenant_id=? ORDER BY d.document_id LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def list_versions(self, tenant_id: str, document_id: str, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("spreadsheet_document_list_limit_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        document = require_id(document_id, "document_id")
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM spreadsheet_documents WHERE tenant_id=? AND document_id=?",
                (tenant, document),
            ).fetchone()
            rows = connection.execute(
                "SELECT payload_json FROM spreadsheet_versions WHERE tenant_id=? AND document_id=? "
                "ORDER BY version DESC LIMIT ?",
                (tenant, document, limit),
            ).fetchall()
        if not exists:
            raise KeyError("spreadsheet_document_not_found")
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def get_proposal(self, tenant_id: str, proposal_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM spreadsheet_proposals WHERE tenant_id=? AND proposal_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(proposal_id, "proposal_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def referenced_artifact_digests(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM spreadsheet_versions").fetchall()
        digests: set[str] = set()
        for row in rows:
            payload = json.loads(row[0])
            for field in ("source_artifact", "published_artifact", "candidate_artifact"):
                artifact = payload.get(field)
                digest = artifact.get("sha256") if isinstance(artifact, dict) else None
                if isinstance(digest, str) and len(digest) == 64:
                    digests.add(digest)
        return digests

    def finalize_proposal(
        self,
        tenant_id: str,
        proposal_id: str,
        result: Mapping[str, Any],
        *,
        document_id: str,
        expected_version: int,
        promoted_document: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        proposal = require_id(proposal_id, "proposal_id")
        value = dict(result)
        with self._transaction, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM spreadsheet_proposals WHERE tenant_id=? AND proposal_id=?",
                (tenant, proposal),
            ).fetchone()
            if existing:
                previous = json.loads(existing[0])
                if previous.get("proposal_digest") != value.get("proposal_digest"):
                    raise SpreadsheetStoreConflict("spreadsheet_proposal_replay_conflict")
                return {**previous, "replayed": True}
            if promoted_document is not None:
                document = require_id(document_id, "document_id")
                row = connection.execute(
                    "SELECT current_version FROM spreadsheet_documents WHERE tenant_id=? AND document_id=?",
                    (tenant, document),
                ).fetchone()
                if not row:
                    raise KeyError("spreadsheet_document_not_found")
                if int(row[0]) != expected_version:
                    raise SpreadsheetStoreConflict("spreadsheet_document_version_conflict")
                version = expected_version + 1
                published = {
                    **dict(promoted_document),
                    "tenant_id": tenant,
                    "document_id": document,
                    "version": version,
                }
                connection.execute(
                    "INSERT INTO spreadsheet_versions(tenant_id,document_id,version,payload_json) VALUES(?,?,?,?)",
                    (tenant, document, version, canonical_json(published)),
                )
                connection.execute(
                    "UPDATE spreadsheet_documents SET current_version=? WHERE tenant_id=? AND document_id=? AND current_version=?",
                    (version, tenant, document, expected_version),
                )
                value["promoted_version"] = version
            connection.execute(
                "INSERT INTO spreadsheet_proposals(tenant_id,proposal_id,payload_json) VALUES(?,?,?)",
                (tenant, proposal, canonical_json(value)),
            )
        return {**value, "replayed": False}

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS spreadsheet_documents(tenant_id TEXT,document_id TEXT,current_version INTEGER,PRIMARY KEY(tenant_id,document_id));
                CREATE TABLE IF NOT EXISTS spreadsheet_versions(tenant_id TEXT,document_id TEXT,version INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,document_id,version));
                CREATE TABLE IF NOT EXISTS spreadsheet_proposals(tenant_id TEXT,proposal_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,proposal_id));
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    @staticmethod
    def _version_number(version: int) -> int:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("spreadsheet_document_version_invalid")
        return version


__all__ = ["SpreadsheetStore", "SpreadsheetStoreConflict"]
