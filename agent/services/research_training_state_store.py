"""Immutable tenant-scoped SQLite revisions for research runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.research_training import canonical_json, require_id


class ResearchTrainingStateConflict(RuntimeError):
    pass


class ResearchTrainingStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def create(self, payload: Mapping[str, Any], *, idempotency_digest: str) -> tuple[dict[str, Any], bool]:
        tenant = require_id(payload.get("tenant_id"), "tenant_id")
        run_id = require_id(payload.get("run_id"), "run_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM research_training_idempotency WHERE tenant_id=? AND key_digest=?",
                (tenant, idempotency_digest),
            ).fetchone()
            if row:
                return self._get(connection, tenant, str(row[0])), True
            value = {**dict(payload), "revision": 1}
            connection.execute(
                "INSERT INTO research_training_run_revisions(tenant_id,run_id,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, run_id, 1, canonical_json(value)),
            )
            connection.execute(
                "INSERT INTO research_training_idempotency(tenant_id,key_digest,run_id) VALUES(?,?,?)",
                (tenant, idempotency_digest, run_id),
            )
        return value, False

    def append(
        self, tenant_id: str, run_id: str, payload: Mapping[str, Any], *, expected_revision: int
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        with self._transaction, self._connect() as connection:
            current = self._get(connection, tenant, run)
            if current["revision"] != expected_revision:
                raise ResearchTrainingStateConflict("research_run_revision_conflict")
            value = {**dict(payload), "tenant_id": tenant, "run_id": run, "revision": expected_revision + 1}
            connection.execute(
                "INSERT INTO research_training_run_revisions(tenant_id,run_id,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, run, expected_revision + 1, canonical_json(value)),
            )
        return value

    def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._get(connection, require_id(tenant_id, "tenant_id"), require_id(run_id, "run_id"))

    def list(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        if not 1 <= limit <= 100:
            raise ValueError("research_run_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM research_training_run_revisions candidate WHERE tenant_id=? "
                "AND revision=(SELECT MAX(revision) FROM research_training_run_revisions "
                "WHERE tenant_id=candidate.tenant_id AND run_id=candidate.run_id) ORDER BY run_id LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    @staticmethod
    def _get(connection: sqlite3.Connection, tenant_id: str, run_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT payload_json FROM research_training_run_revisions WHERE tenant_id=? AND run_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (tenant_id, run_id),
        ).fetchone()
        if not row:
            raise KeyError("research_run_not_found")
        return json.loads(row[0])

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_training_run_revisions("
                "tenant_id TEXT NOT NULL,run_id TEXT NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,"
                "PRIMARY KEY(tenant_id,run_id,revision))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_training_idempotency("
                "tenant_id TEXT NOT NULL,key_digest TEXT NOT NULL,run_id TEXT NOT NULL,"
                "PRIMARY KEY(tenant_id,key_digest))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["ResearchTrainingStateConflict", "ResearchTrainingStateStore"]
