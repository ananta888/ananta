"""Immutable research artifact lineage indexed by tenant and digest."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.research_training import ResearchArtifactManifestV1, canonical_json, require_digest, require_id


class ResearchTrainingLineageService:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def register(self, *, manifest: Mapping[str, Any], artifact_ref: str) -> dict[str, Any]:
        parsed = ResearchArtifactManifestV1.from_mapping(manifest)
        reference = self._artifact_ref(artifact_ref)
        with self._transaction, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM research_training_lineage WHERE tenant_id=? AND artifact_digest=?",
                (parsed.tenant_id, parsed.artifact_digest),
            ).fetchone()
            if existing:
                value = json.loads(existing[0])
                if value["manifest"] != parsed.to_dict() or value["artifact_ref"] != reference:
                    raise ValueError("research_lineage_replay_conflict")
                return {**value, "replayed": True}
            for parent in parsed.parent_artifact_digests:
                row = connection.execute(
                    "SELECT 1 FROM research_training_lineage WHERE tenant_id=? AND artifact_digest=?",
                    (parsed.tenant_id, parent),
                ).fetchone()
                if not row:
                    raise ValueError("research_lineage_parent_missing")
            value = {
                "schema": "ananta.research-training-lineage-entry.v1",
                "tenant_id": parsed.tenant_id,
                "run_id": parsed.run_id,
                "artifact_digest": parsed.artifact_digest,
                "artifact_ref": reference,
                "manifest": parsed.to_dict(),
                "replayed": False,
            }
            connection.execute(
                "INSERT INTO research_training_lineage(tenant_id,artifact_digest,run_id,payload_json) VALUES(?,?,?,?)",
                (parsed.tenant_id, parsed.artifact_digest, parsed.run_id, canonical_json(value)),
            )
        return value

    def get(self, *, tenant_id: str, artifact_digest: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        digest = require_digest(artifact_digest, "artifact_digest")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM research_training_lineage WHERE tenant_id=? AND artifact_digest=?",
                (tenant, digest),
            ).fetchone()
        if not row:
            raise KeyError("research_lineage_not_found")
        return json.loads(row[0])

    def list_run(self, *, tenant_id: str, run_id: str, limit: int = 100) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        if not 1 <= limit <= 100:
            raise ValueError("research_lineage_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM research_training_lineage WHERE tenant_id=? AND run_id=? "
                "ORDER BY artifact_digest LIMIT ?",
                (tenant, run, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def referenced_digests(self, *, tenant_id: str) -> list[str]:
        tenant = require_id(tenant_id, "tenant_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM research_training_lineage WHERE tenant_id=?",
                (tenant,),
            ).fetchall()
        referenced: set[str] = set()
        for row in rows:
            value = json.loads(row[0])
            manifest = dict(value.get("manifest") or {})
            referenced.update(str(item) for item in manifest.get("parent_artifact_digests") or [])
        return sorted(referenced)

    def delete_leaf(self, *, tenant_id: str, artifact_digest: str) -> None:
        tenant = require_id(tenant_id, "tenant_id")
        digest = require_digest(artifact_digest, "artifact_digest")
        with self._transaction, self._connect() as connection:
            rows = connection.execute(
                "SELECT artifact_digest,payload_json FROM research_training_lineage WHERE tenant_id=?",
                (tenant,),
            ).fetchall()
            if any(
                digest in set(dict(json.loads(row[1]).get("manifest") or {}).get("parent_artifact_digests") or [])
                for row in rows
            ):
                raise ValueError("research_lineage_artifact_referenced")
            cursor = connection.execute(
                "DELETE FROM research_training_lineage WHERE tenant_id=? AND artifact_digest=?",
                (tenant, digest),
            )
            if cursor.rowcount != 1:
                raise KeyError("research_lineage_not_found")

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_training_lineage("
                "tenant_id TEXT NOT NULL,artifact_digest TEXT NOT NULL,run_id TEXT NOT NULL,payload_json TEXT NOT NULL,"
                "PRIMARY KEY(tenant_id,artifact_digest))"
            )

    @staticmethod
    def _artifact_ref(value: object) -> str:
        text = str(value or "").strip()
        path = PurePosixPath(text)
        if (
            not text
            or len(text) > 512
            or path.is_absolute()
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
        ):
            raise ValueError("research_artifact_ref_invalid")
        return path.as_posix()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["ResearchTrainingLineageService"]
