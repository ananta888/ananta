"""Immutable Hub-side storage for delegated research assignments."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.research_training import canonical_json, require_digest, require_id
from ananta_contracts.research_training_execution import ResearchStageAssignmentV1


class ResearchTrainingAssignmentStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def put(self, assignment: Mapping[str, Any], *, worker_authorization: str) -> dict[str, Any]:
        parsed = ResearchStageAssignmentV1.from_mapping(assignment)
        authorization = str(worker_authorization or "")
        if len(authorization) != 64 or any(char not in "0123456789abcdef" for char in authorization):
            raise ValueError("research_worker_authorization_invalid")
        payload = parsed.to_dict()
        digest = parsed.digest
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT assignment_digest,payload_json,state,result_digest,worker_authorization "
                "FROM research_training_assignments "
                "WHERE tenant_id=? AND assignment_id=?",
                (parsed.run_spec.tenant_id, parsed.assignment_id),
            ).fetchone()
            if row:
                if (
                    str(row[0]) != digest
                    or json.loads(row[1]) != payload
                    or str(row[4]) != authorization
                ):
                    raise ValueError("research_assignment_replay_conflict")
                return self._projection(payload, digest, str(row[2]), row[3], True)
            connection.execute(
                "INSERT INTO research_training_assignments"
                "(tenant_id,assignment_id,assignment_digest,payload_json,state,result_digest,worker_authorization,"
                "worker_id,run_id,stage_id,attempt_id) VALUES(?,?,?,?,?,NULL,?,?,?,?,?)",
                (
                    parsed.run_spec.tenant_id,
                    parsed.assignment_id,
                    digest,
                    canonical_json(payload),
                    "reserved",
                    authorization,
                    parsed.worker_id,
                    parsed.run_id,
                    parsed.stage.stage_id,
                    parsed.attempt_id,
                ),
            )
        return self._projection(payload, digest, "reserved", None, False)

    def get(self, *, tenant_id: str, assignment_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        assignment = require_id(assignment_id, "assignment_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT assignment_digest,payload_json,state,result_digest FROM research_training_assignments "
                "WHERE tenant_id=? AND assignment_id=?",
                (tenant, assignment),
            ).fetchone()
        if row is None:
            raise KeyError("research_assignment_not_found")
        payload = json.loads(row[1])
        return self._projection(payload, str(row[0]), str(row[2]), row[3], False)

    def accept(self, *, tenant_id: str, assignment_id: str, result_digest: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        assignment = require_id(assignment_id, "assignment_id")
        digest = require_digest(result_digest, "result_digest")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT assignment_digest,payload_json,state,result_digest FROM research_training_assignments "
                "WHERE tenant_id=? AND assignment_id=?",
                (tenant, assignment),
            ).fetchone()
            if row is None:
                raise KeyError("research_assignment_not_found")
            if str(row[2]) == "accepted":
                if str(row[3]) != digest:
                    raise ValueError("research_assignment_result_replay_conflict")
                return self._projection(json.loads(row[1]), str(row[0]), "accepted", digest, True)
            if str(row[2]) != "reserved":
                raise ValueError("research_assignment_state_invalid")
            connection.execute(
                "UPDATE research_training_assignments SET state='accepted',result_digest=? "
                "WHERE tenant_id=? AND assignment_id=? AND state='reserved'",
                (digest, tenant, assignment),
            )
        return self._projection(json.loads(row[1]), str(row[0]), "accepted", digest, False)

    def worker_authorization(self, *, tenant_id: str, assignment_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT worker_authorization FROM research_training_assignments "
                "WHERE tenant_id=? AND assignment_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(assignment_id, "assignment_id")),
            ).fetchone()
        if row is None or not str(row[0]):
            raise KeyError("research_assignment_not_found")
        return str(row[0])

    def resolve_for_worker(self, *, assignment_id: str, worker_id: str) -> dict[str, Any]:
        assignment = require_id(assignment_id, "assignment_id")
        worker = require_id(worker_id, "worker_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT assignment_digest,payload_json,state,result_digest "
                "FROM research_training_assignments WHERE assignment_id=?",
                (assignment,),
            ).fetchall()
        if len(rows) != 1:
            raise PermissionError("research_worker_assignment_unavailable")
        payload = json.loads(rows[0][1])
        if payload.get("worker_id") != worker:
            raise PermissionError("research_worker_assignment_binding_invalid")
        return self._projection(payload, str(rows[0][0]), str(rows[0][2]), rows[0][3], False)

    def resolve_attempt(
        self,
        *,
        worker_id: str,
        run_id: str,
        stage_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        worker = require_id(worker_id, "worker_id")
        identifiers = {
            "run_id": require_id(run_id, "run_id"),
            "stage_id": require_id(stage_id, "stage_id"),
            "attempt_id": require_id(attempt_id, "attempt_id"),
        }
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT assignment_digest,payload_json,state,result_digest "
                "FROM research_training_assignments "
                "WHERE worker_id=? AND run_id=? AND stage_id=? AND attempt_id=?",
                (
                    worker,
                    identifiers["run_id"],
                    identifiers["stage_id"],
                    identifiers["attempt_id"],
                ),
            ).fetchall()
        matches = []
        for row in rows:
            payload = json.loads(row[1])
            if (
                payload.get("worker_id") == worker
                and payload.get("run_id") == identifiers["run_id"]
                and dict(payload.get("stage") or {}).get("stage_id") == identifiers["stage_id"]
                and payload.get("attempt_id") == identifiers["attempt_id"]
            ):
                matches.append((row, payload))
        if len(matches) != 1:
            raise PermissionError("research_worker_assignment_unavailable")
        row, payload = matches[0]
        return self._projection(payload, str(row[0]), str(row[2]), row[3], False)

    @staticmethod
    def _projection(
        payload: Mapping[str, Any], digest: str, state: str, result_digest: object, replayed: bool
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-assignment-record.v1",
            "assignment": dict(payload),
            "assignment_digest": digest,
            "state": state,
            "result_digest": str(result_digest) if result_digest else None,
            "replayed": replayed,
        }

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_training_assignments("
                "tenant_id TEXT NOT NULL,assignment_id TEXT NOT NULL,assignment_digest TEXT NOT NULL,"
                "payload_json TEXT NOT NULL,state TEXT NOT NULL,result_digest TEXT,worker_authorization TEXT NOT NULL,"
                "worker_id TEXT,run_id TEXT,stage_id TEXT,attempt_id TEXT,"
                "PRIMARY KEY(tenant_id,assignment_id))"
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(research_training_assignments)").fetchall()
            }
            for column in ("worker_id", "run_id", "stage_id", "attempt_id"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE research_training_assignments ADD COLUMN {column} TEXT"
                    )
            rows = connection.execute(
                "SELECT tenant_id,assignment_id,payload_json FROM research_training_assignments "
                "WHERE worker_id IS NULL OR run_id IS NULL OR stage_id IS NULL OR attempt_id IS NULL"
            ).fetchall()
            for tenant_id, assignment_id, payload_json in rows:
                payload = json.loads(payload_json)
                connection.execute(
                    "UPDATE research_training_assignments SET worker_id=?,run_id=?,stage_id=?,attempt_id=? "
                    "WHERE tenant_id=? AND assignment_id=?",
                    (
                        payload["worker_id"],
                        payload["run_id"],
                        payload["stage"]["stage_id"],
                        payload["attempt_id"],
                        tenant_id,
                        assignment_id,
                    ),
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_research_assignment_attempt "
                "ON research_training_assignments(worker_id,run_id,stage_id,attempt_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["ResearchTrainingAssignmentStore"]
