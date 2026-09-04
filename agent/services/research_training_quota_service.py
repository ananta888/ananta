"""Durable storage reservations and retention metadata for research artifacts."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.research_training import require_digest, require_id


class ResearchTrainingQuotaService:
    def __init__(
        self,
        path: str | Path,
        *,
        maximum_bytes_per_tenant: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._maximum = int(maximum_bytes_per_tenant)
        self._clock = clock
        if not 1 <= self._maximum <= 1 << 60:
            raise ValueError("research_quota_limit_invalid")
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def reserve(
        self,
        *,
        tenant_id: str,
        reservation_id: str,
        expected_bytes: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        reservation = require_id(reservation_id, "quota_reservation_id")
        if not 1 <= expected_bytes <= self._maximum or not 1 <= lease_seconds <= 86_400:
            raise ValueError("research_quota_reservation_invalid")
        now = float(self._clock())
        with self._transaction, self._connect() as connection:
            self._expire(connection, now)
            existing = connection.execute(
                "SELECT expected_bytes,expires_at,state FROM research_quota_reservations "
                "WHERE tenant_id=? AND reservation_id=?",
                (tenant, reservation),
            ).fetchone()
            if existing:
                if int(existing[0]) != expected_bytes or str(existing[2]) != "reserved":
                    raise ValueError("research_quota_reservation_replay_conflict")
                return self._projection(tenant, reservation, expected_bytes, float(existing[1]), True)
            committed = int(
                connection.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM research_quota_artifacts WHERE tenant_id=?",
                    (tenant,),
                ).fetchone()[0]
            )
            reserved = int(
                connection.execute(
                    "SELECT COALESCE(SUM(expected_bytes),0) FROM research_quota_reservations "
                    "WHERE tenant_id=? AND state='reserved'",
                    (tenant,),
                ).fetchone()[0]
            )
            if committed + reserved + expected_bytes > self._maximum:
                raise ValueError("research_storage_quota_exceeded")
            expires = now + lease_seconds
            connection.execute(
                "INSERT INTO research_quota_reservations"
                "(tenant_id,reservation_id,expected_bytes,expires_at,state) VALUES(?,?,?,?,?)",
                (tenant, reservation, expected_bytes, expires, "reserved"),
            )
        return self._projection(tenant, reservation, expected_bytes, expires, False)

    def finalize(
        self,
        *,
        tenant_id: str,
        reservation_id: str,
        artifact_digest: str,
        artifact_ref: str,
        actual_bytes: int,
        retention_class: str,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        reservation = require_id(reservation_id, "quota_reservation_id")
        digest = require_digest(artifact_digest, "artifact_digest")
        retention = require_id(retention_class, "retention_class")
        if retention not in {"ephemeral", "checkpoint", "promoted"}:
            raise ValueError("research_retention_class_invalid")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT expected_bytes,expires_at,state FROM research_quota_reservations "
                "WHERE tenant_id=? AND reservation_id=?",
                (tenant, reservation),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    "SELECT reservation_id,size_bytes,artifact_ref,retention_class FROM research_quota_artifacts "
                    "WHERE tenant_id=? AND artifact_digest=?",
                    (tenant, digest),
                ).fetchone()
                if existing and tuple(existing) == (reservation, actual_bytes, artifact_ref, retention):
                    return self._artifact_projection(tenant, digest, artifact_ref, actual_bytes, retention, True)
                raise ValueError("research_quota_reservation_missing")
            if str(row[2]) != "reserved" or float(row[1]) <= self._clock():
                raise ValueError("research_quota_reservation_expired")
            if not 1 <= actual_bytes <= int(row[0]):
                raise ValueError("research_quota_actual_size_invalid")
            connection.execute(
                "INSERT INTO research_quota_artifacts"
                "(tenant_id,artifact_digest,reservation_id,artifact_ref,size_bytes,retention_class,pinned) "
                "VALUES(?,?,?,?,?,?,?)",
                (tenant, digest, reservation, artifact_ref, actual_bytes, retention, int(retention == "promoted")),
            )
            connection.execute(
                "DELETE FROM research_quota_reservations WHERE tenant_id=? AND reservation_id=?",
                (tenant, reservation),
            )
        return self._artifact_projection(tenant, digest, artifact_ref, actual_bytes, retention, False)

    def pin(self, *, tenant_id: str, artifact_digest: str, pinned: bool) -> None:
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE research_quota_artifacts SET pinned=? WHERE tenant_id=? AND artifact_digest=?",
                (
                    int(bool(pinned)),
                    require_id(tenant_id, "tenant_id"),
                    require_digest(artifact_digest, "artifact_digest"),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("research_quota_artifact_not_found")

    def release(self, *, tenant_id: str, reservation_id: str) -> bool:
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM research_quota_reservations WHERE tenant_id=? AND reservation_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(reservation_id, "quota_reservation_id")),
            )
            return cursor.rowcount == 1

    def garbage_candidates(
        self,
        *,
        tenant_id: str,
        referenced_digests: Sequence[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        referenced = {require_digest(item, "artifact_digest") for item in referenced_digests}
        if not 1 <= limit <= 1000:
            raise ValueError("research_gc_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT artifact_digest,artifact_ref,size_bytes,retention_class FROM research_quota_artifacts "
                "WHERE tenant_id=? AND pinned=0 AND retention_class='ephemeral' ORDER BY artifact_digest LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return [
            {
                "artifact_digest": str(row[0]),
                "artifact_ref": str(row[1]),
                "size_bytes": int(row[2]),
                "retention_class": str(row[3]),
            }
            for row in rows
            if str(row[0]) not in referenced
        ]

    def forget(self, *, tenant_id: str, artifact_digest: str) -> None:
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM research_quota_artifacts WHERE tenant_id=? AND artifact_digest=? AND pinned=0",
                (require_id(tenant_id, "tenant_id"), require_digest(artifact_digest, "artifact_digest")),
            )
            if cursor.rowcount != 1:
                raise ValueError("research_gc_artifact_not_deletable")

    @staticmethod
    def _expire(connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "DELETE FROM research_quota_reservations WHERE state='reserved' AND expires_at<=?", (now,)
        )

    @staticmethod
    def _projection(
        tenant: str, reservation: str, expected_bytes: int, expires: float, replayed: bool
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-quota-reservation.v1",
            "tenant_id": tenant,
            "reservation_id": reservation,
            "expected_bytes": expected_bytes,
            "expires_at_epoch": expires,
            "replayed": replayed,
        }

    @staticmethod
    def _artifact_projection(
        tenant: str,
        digest: str,
        artifact_ref: str,
        size: int,
        retention: str,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-quota-artifact.v1",
            "tenant_id": tenant,
            "artifact_digest": digest,
            "artifact_ref": artifact_ref,
            "size_bytes": size,
            "retention_class": retention,
            "replayed": replayed,
        }

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_quota_reservations("
                "tenant_id TEXT NOT NULL,reservation_id TEXT NOT NULL,expected_bytes INTEGER NOT NULL,"
                "expires_at REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(tenant_id,reservation_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_quota_artifacts("
                "tenant_id TEXT NOT NULL,artifact_digest TEXT NOT NULL,reservation_id TEXT NOT NULL,"
                "artifact_ref TEXT NOT NULL,size_bytes INTEGER NOT NULL,retention_class TEXT NOT NULL,"
                "pinned INTEGER NOT NULL,PRIMARY KEY(tenant_id,artifact_digest),"
                "UNIQUE(tenant_id,reservation_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["ResearchTrainingQuotaService"]
