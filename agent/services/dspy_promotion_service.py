"""Atomic policy-controlled promotion, canary assignment and rollback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.dspy_optimization import canonical_json, require_digest, require_id


class DspyPromotionConflict(RuntimeError):
    pass


class DspyPromotionService:
    def __init__(self, path: str | Path, *, attestations: DspyEvaluationAttestationService) -> None:
        self._path = Path(path)
        self._attestations = attestations
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def promote(
        self,
        *,
        tenant_id: str,
        scope_id: str,
        candidate_digest: str,
        baseline_digest: str,
        evaluation: Mapping[str, Any],
        expected_revision: int,
        canary_percent: int = 100,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        scope = require_id(scope_id, "scope_id")
        candidate = require_digest(candidate_digest, "candidate_digest")
        baseline = require_digest(baseline_digest, "baseline_digest")
        if not self._attestations.verify(evaluation):
            raise PermissionError("dspy_evaluation_attestation_invalid")
        program_bound = (
            evaluation.get("candidate_program_digest") == candidate
            and evaluation.get("baseline_program_digest") == baseline
        )
        if not program_bound:
            raise PermissionError("dspy_evaluation_program_binding_invalid")
        if not evaluation.get("promotion_eligible") or evaluation.get("reason_codes"):
            raise PermissionError("dspy_promotion_gate_failed")
        if not 1 <= canary_percent <= 100:
            raise ValueError("dspy_canary_percent_invalid")
        return self._append(
            tenant,
            scope,
            {
                "active_digest": candidate,
                "previous_digest": baseline,
                "evaluation_digest": require_digest(evaluation.get("evaluation_digest"), "evaluation_digest"),
                "canary_percent": canary_percent,
                "state": "active",
                "reason_code": "dspy_promoted_by_policy",
                "updated_at": _now(),
                "human_intervention_required": False,
            },
            expected_revision,
        )

    def rollback(self, *, tenant_id: str, scope_id: str, expected_revision: int) -> dict[str, Any]:
        current = self.get(tenant_id=tenant_id, scope_id=scope_id)
        previous = current.get("previous_digest")
        if not previous:
            raise PermissionError("dspy_rollback_target_missing")
        return self._append(
            tenant_id,
            scope_id,
            {
                **current,
                "active_digest": previous,
                "previous_digest": current.get("active_digest"),
                "state": "rolled_back",
                "reason_code": "dspy_rollback_applied",
                "updated_at": _now(),
                "human_intervention_required": False,
            },
            expected_revision,
        )

    def assignment(self, *, tenant_id: str, scope_id: str, subject_id: str) -> dict[str, Any]:
        current = self.get(tenant_id=tenant_id, scope_id=scope_id)
        bucket = (
            int(
                hashlib.sha256(f"{tenant_id}\0{scope_id}\0{subject_id}\0{current['revision']}".encode()).hexdigest()[
                    :8
                ],
                16,
            )
            % 100
        )
        selected = bucket < int(current["canary_percent"])
        return {
            "program_digest": current["active_digest"] if selected else current["previous_digest"],
            "variant": "candidate" if selected else "baseline",
            "registry_revision": current["revision"],
        }

    def get(self, *, tenant_id: str, scope_id: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        scope = require_id(scope_id, "scope_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM dspy_program_registry WHERE tenant_id=? AND scope_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (tenant, scope),
            ).fetchone()
        if not row:
            raise KeyError("dspy_registry_scope_not_found")
        return json.loads(row[0])

    def _append(
        self, tenant_id: str, scope_id: str, payload: Mapping[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        scope = require_id(scope_id, "scope_id")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(revision) FROM dspy_program_registry WHERE tenant_id=? AND scope_id=?", (tenant, scope)
            ).fetchone()
            current = int(row[0] or 0)
            if current != expected_revision:
                raise DspyPromotionConflict("dspy_registry_revision_conflict")
            value = {**dict(payload), "tenant_id": tenant, "scope_id": scope, "revision": current + 1}
            value.pop("entity_kind", None)
            connection.execute(
                "INSERT INTO dspy_program_registry(tenant_id,scope_id,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, scope, current + 1, canonical_json(value)),
            )
        return value

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dspy_program_registry(tenant_id TEXT NOT NULL,scope_id TEXT NOT NULL,"
                "revision INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(tenant_id,scope_id,revision))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DspyPromotionConflict", "DspyPromotionService"]
