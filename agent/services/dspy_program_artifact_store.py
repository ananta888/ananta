"""Tenant-scoped, content-addressed prompt-program artifact storage."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.dspy_optimization import PromptProgramV1, canonical_json, require_id


class DspyProgramArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._transaction = InterProcessFileTransaction(self._root / ".artifact.lock")
        self._registry = self._root / ".artifact-registry.sqlite3"
        self._initialize()

    def put(
        self,
        *,
        tenant_id: str,
        run_id: str,
        program: PromptProgramV1,
        retention_days: int = 90,
        access_class: str = "tenant_operators",
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        if program.tenant_id != tenant:
            raise PermissionError("dspy_artifact_tenant_mismatch")
        payload = canonical_json(program.to_dict()).encode()
        if not 1 <= retention_days <= 3_650 or access_class not in {"tenant_operators", "promotion_runtime"}:
            raise ValueError("dspy_artifact_retention_invalid")
        digest = program.digest
        tenant_dir = self._root / tenant
        target_candidate = tenant_dir / run
        if tenant_dir.is_symlink() or target_candidate.is_symlink():
            raise ValueError("dspy_artifact_symlink_denied")
        target_dir = target_candidate.resolve()
        if self._root not in target_dir.parents:
            raise ValueError("dspy_artifact_path_invalid")
        artifact = {
            "schema": program.schema,
            "digest": digest,
            "size_bytes": len(payload),
            "media_type": "application/vnd.ananta.prompt-program+json",
            "producer_run_id": run,
            "tenant_id": tenant,
            "artifact_ref": f"dspy-program:{tenant}:{run}:{digest}",
        }
        with self._transaction:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target_dir.is_symlink() or target_dir.resolve() != target_dir:
                raise ValueError("dspy_artifact_symlink_denied")
            target = target_dir / f"{digest}.json"
            if target.is_symlink():
                raise ValueError("dspy_artifact_symlink_denied")
            if target.exists():
                if target.read_bytes() != payload:
                    raise RuntimeError("dspy_artifact_digest_collision")
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".dspy-", dir=target_dir)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, 0o600)
                    os.link(temporary, target, follow_symlinks=False)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            now = datetime.now(timezone.utc)
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO dspy_artifacts(tenant_id,run_id,digest,artifact_json,created_at,"
                    "retention_until,access_class,legal_hold,promotion_refs) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        tenant,
                        run,
                        digest,
                        canonical_json(artifact),
                        _time(now),
                        _time(now + timedelta(days=retention_days)),
                        access_class,
                        0,
                        0,
                    ),
                )
        return artifact

    def get(self, *, tenant_id: str, run_id: str, digest: str) -> PromptProgramV1:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        if len(digest) != 64:
            raise ValueError("dspy_artifact_digest_invalid")
        target = (self._root / tenant / run / f"{digest}.json").resolve()
        if self._root not in target.parents or target.is_symlink():
            raise ValueError("dspy_artifact_path_invalid")
        payload = target.read_bytes()
        import json

        raw = json.loads(payload)
        program = PromptProgramV1.from_mapping(raw)
        if program.digest != digest:
            raise RuntimeError("dspy_artifact_digest_mismatch")
        return program

    def set_legal_hold(self, *, tenant_id: str, run_id: str, digest: str, enabled: bool) -> None:
        tenant, run = require_id(tenant_id, "tenant_id"), require_id(run_id, "run_id")
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE dspy_artifacts SET legal_hold=? WHERE tenant_id=? AND run_id=? AND digest=?",
                (1 if enabled else 0, tenant, run, digest),
            )
            if cursor.rowcount != 1:
                raise KeyError("dspy_artifact_not_found")

    def bind_promotion(self, *, tenant_id: str, run_id: str, digest: str, delta: int) -> None:
        if delta not in {-1, 1}:
            raise ValueError("dspy_artifact_promotion_delta_invalid")
        tenant, run = require_id(tenant_id, "tenant_id"), require_id(run_id, "run_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT promotion_refs FROM dspy_artifacts WHERE tenant_id=? AND run_id=? AND digest=?",
                (tenant, run, digest),
            ).fetchone()
            if not row or int(row[0]) + delta < 0:
                raise KeyError("dspy_artifact_not_found")
            connection.execute(
                "UPDATE dspy_artifacts SET promotion_refs=? WHERE tenant_id=? AND run_id=? AND digest=?",
                (int(row[0]) + delta, tenant, run, digest),
            )

    def retention_sweep(self, *, now: str) -> dict[str, Any]:
        removed: list[str] = []
        with self._transaction, self._connect() as connection:
            rows = connection.execute(
                "SELECT tenant_id,run_id,digest FROM dspy_artifacts WHERE retention_until<=? "
                "AND legal_hold=0 AND promotion_refs=0 ORDER BY tenant_id,run_id,digest",
                (now,),
            ).fetchall()
            for tenant, run, digest in rows:
                target = (self._root / tenant / run / f"{digest}.json").resolve()
                if self._root not in target.parents or target.is_symlink():
                    raise ValueError("dspy_artifact_path_invalid")
                target.unlink(missing_ok=True)
                connection.execute(
                    "DELETE FROM dspy_artifacts WHERE tenant_id=? AND run_id=? AND digest=?", (tenant, run, digest)
                )
                removed.append(f"dspy-program:{tenant}:{run}:{digest}")
        return {"removed": removed, "count": len(removed)}

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dspy_artifacts(tenant_id TEXT NOT NULL,run_id TEXT NOT NULL,"
                "digest TEXT NOT NULL,artifact_json TEXT NOT NULL,created_at TEXT NOT NULL,"
                "retention_until TEXT NOT NULL,"
                "access_class TEXT NOT NULL,legal_hold INTEGER NOT NULL,promotion_refs INTEGER NOT NULL,"
                "PRIMARY KEY(tenant_id,run_id,digest))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._registry, timeout=5.0)


def _time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["DspyProgramArtifactStore"]
