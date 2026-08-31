"""Separate immutable Memory Pack registry, composition and runtime gates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.dendritic_memory_evaluation_attestation import DendriticMemoryEvaluationAttestation
from agent.services.dendritic_memory_migration import upgrade_registry_store
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_runtime_gate import DendriticMemoryRuntimeGate
from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.dendritic_memory import (
    DendriticMemoryPackManifestV1,
    canonical_digest,
    canonical_json,
    require_digest,
    require_id,
)


class DendriticMemoryRegistryConflict(RuntimeError):
    pass


class DendriticMemoryRegistryService:
    def __init__(
        self,
        path: str | Path,
        *,
        policy: DendriticMemoryPolicy,
        attestations: DendriticMemoryEvaluationAttestation,
        runtime_gate: DendriticMemoryRuntimeGate,
    ) -> None:
        self._path = Path(path)
        self._policy = policy
        self._attestations = attestations
        self._runtime_gate = runtime_gate
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def quarantine(
        self,
        *,
        manifest: Mapping[str, Any],
        artifact_ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = DendriticMemoryPackManifestV1.from_mapping(manifest)
        return self._create(
            parsed,
            artifact_ref=require_id(artifact_ref, "artifact_ref"),
            state="quarantined",
            reason_code="dendritic_pack_quarantined",
            idempotency_key=idempotency_key,
            action="import",
        )

    def approve_evaluated(
        self,
        *,
        tenant_id: str,
        pack_digest: str,
        evaluation: Mapping[str, Any],
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self._attestations.verify(evaluation):
            raise PermissionError("dendritic_evaluation_attestation_invalid")
        if not evaluation.get("experiment_eligible") or evaluation.get("reason_codes"):
            raise PermissionError("dendritic_evaluation_gate_failed")
        current = self.get(tenant_id=tenant_id, pack_digest=pack_digest)
        if evaluation.get("dendritic_pack_digest") != current["pack_digest"]:
            raise PermissionError("dendritic_evaluation_pack_binding_invalid")
        return self._append(
            current,
            state="approved_for_experiment",
            reason_code="dendritic_pack_approved_by_policy",
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            extra={
                "evaluation_digest": require_digest(
                    evaluation.get("evaluation_digest"), "evaluation_digest"
                ),
                "approval": {
                    "policy_actor": "automatic-policy",
                    "scope": "experimental-runtime",
                    "report_id": require_digest(
                        evaluation.get("evaluation_digest"), "evaluation_digest"
                    ),
                    "artifact_hash": current["pack_digest"],
                    "decided_at": _now(),
                },
            },
            action="approve",
        )

    def compose(
        self,
        *,
        tenant_id: str,
        parent_pack_digests: Sequence[str],
        output_manifest: Mapping[str, Any],
        artifact_ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parents = [self.get(tenant_id=tenant_id, pack_digest=digest) for digest in parent_pack_digests]
        if not 2 <= len(parents) <= 16 or any(parent["state"] != "approved_for_experiment" for parent in parents):
            raise PermissionError("dendritic_composition_parent_denied")
        manifests = [DendriticMemoryPackManifestV1.from_mapping(parent["manifest"]) for parent in parents]
        base_bindings = {
            (item.base_model_id, item.base_model_snapshot_digest, item.architecture_version) for item in manifests
        }
        if len(base_bindings) != 1:
            raise ValueError("dendritic_composition_base_mismatch")
        targets = [target for item in manifests for target in item.target_layers]
        if len(targets) != len(set(targets)):
            raise ValueError("dendritic_composition_target_conflict")
        output = DendriticMemoryPackManifestV1.from_mapping(output_manifest)
        expected_parents = tuple(parent["pack_digest"] for parent in parents)
        if output.tenant_id != tenant_id or output.parent_pack_digests != expected_parents:
            raise ValueError("dendritic_composition_lineage_mismatch")
        return self._create(
            output,
            artifact_ref=require_id(artifact_ref, "artifact_ref"),
            state="quarantined",
            reason_code="dendritic_composition_quarantined",
            idempotency_key=idempotency_key,
            action="compose",
        )

    def revoke(
        self,
        *,
        tenant_id: str,
        pack_digest: str,
        expected_revision: int,
        idempotency_key: str,
        reason_code: str = "dendritic_pack_revoked_by_policy",
    ) -> dict[str, Any]:
        current = self.get(tenant_id=tenant_id, pack_digest=pack_digest)
        with self._connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM dendritic_runtime_routes candidate WHERE tenant_id=? AND pack_digest=? AND active=1 "
                "AND revision=(SELECT MAX(revision) FROM dendritic_runtime_routes WHERE tenant_id=candidate.tenant_id "
                "AND scope_id=candidate.scope_id) LIMIT 1",
                (tenant_id, pack_digest),
            ).fetchone()
        if active:
            raise DendriticMemoryRegistryConflict("dendritic_pack_active_route_conflict")
        return self._append(
            current,
            state="revoked",
            reason_code=require_id(reason_code, "reason_code"),
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            action="revoke",
        )

    def reject(
        self,
        *,
        tenant_id: str,
        pack_digest: str,
        expected_revision: int,
        idempotency_key: str,
        reason_code: str = "dendritic_pack_rejected_by_policy",
    ) -> dict[str, Any]:
        current = self.get(tenant_id=tenant_id, pack_digest=pack_digest)
        if current["state"] not in {"quarantined", "evaluated"}:
            raise DendriticMemoryRegistryConflict("dendritic_pack_reject_state_conflict")
        return self._append(
            current,
            state="rejected",
            reason_code=require_id(reason_code, "reason_code"),
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            action="reject",
        )

    def activate(
        self,
        *,
        tenant_id: str,
        scope_id: str,
        pack_digest: str,
        expected_route_revision: int,
        gate_receipt: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self._runtime_gate.verify(gate_receipt):
            raise PermissionError("dendritic_runtime_gate_attestation_invalid")
        if gate_receipt.get("eligible") is not True or gate_receipt.get("reason_codes"):
            raise PermissionError("dendritic_runtime_gate_failed")
        pack = self.get(tenant_id=tenant_id, pack_digest=pack_digest)
        if pack["state"] != "approved_for_experiment":
            raise PermissionError("dendritic_pack_not_approved")
        if (
            gate_receipt.get("pack_digest") != pack["pack_digest"]
            or gate_receipt.get("base_model_snapshot_digest") != pack["manifest"]["base_model_snapshot_digest"]
        ):
            raise PermissionError("dendritic_runtime_gate_binding_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        scope = require_id(scope_id, "scope_id")
        key_digest = self._idempotency(tenant, idempotency_key)
        with self._transaction, self._connect() as connection:
            replay = self._replay(connection, tenant, key_digest)
            if replay:
                return replay
            row = connection.execute(
                "SELECT MAX(revision) FROM dendritic_runtime_routes WHERE tenant_id=? AND scope_id=?", (tenant, scope)
            ).fetchone()
            current = int(row[0] or 0)
            if current != expected_route_revision:
                raise DendriticMemoryRegistryConflict("dendritic_route_revision_conflict")
            active_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM dendritic_runtime_routes candidate WHERE tenant_id=? AND active=1 "
                    "AND revision=(SELECT MAX(revision) FROM dendritic_runtime_routes "
                    "WHERE tenant_id=candidate.tenant_id AND scope_id=candidate.scope_id)",
                    (tenant,),
                ).fetchone()[0]
            )
            if active_count >= self._policy.max_active_packs:
                raise PermissionError("dendritic_active_pack_limit_exceeded")
            value = {
                "tenant_id": tenant,
                "scope_id": scope,
                "pack_digest": pack["pack_digest"],
                "base_model_snapshot_digest": pack["manifest"]["base_model_snapshot_digest"],
                "revision": current + 1,
                "active": True,
                "gate_receipt_digest": canonical_digest(gate_receipt),
                "reason_code": "dendritic_runtime_activated_by_policy",
                "updated_at": _now(),
                "human_intervention_required": False,
            }
            connection.execute(
                "INSERT INTO dendritic_runtime_routes(tenant_id,scope_id,revision,pack_digest,active,payload_json) "
                "VALUES(?,?,?,?,?,?)",
                (tenant, scope, current + 1, pack["pack_digest"], 1, canonical_json(value)),
            )
            self._record_idempotency(connection, tenant, key_digest, value)
            self._record_audit(connection, value, action="activate", scope=scope)
        return value

    def deactivate(
        self, *, tenant_id: str, scope_id: str, expected_route_revision: int, idempotency_key: str
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        scope = require_id(scope_id, "scope_id")
        key_digest = self._idempotency(tenant, idempotency_key)
        with self._transaction, self._connect() as connection:
            replay = self._replay(connection, tenant, key_digest)
            if replay:
                return replay
            row = connection.execute(
                "SELECT payload_json FROM dendritic_runtime_routes WHERE tenant_id=? AND scope_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (tenant, scope),
            ).fetchone()
            if not row:
                raise KeyError("dendritic_route_not_found")
            current = json.loads(row[0])
            if current["revision"] != expected_route_revision:
                raise DendriticMemoryRegistryConflict("dendritic_route_revision_conflict")
            value = {
                **current,
                "revision": expected_route_revision + 1,
                "active": False,
                "reason_code": "dendritic_runtime_deactivated_by_policy",
                "updated_at": _now(),
            }
            connection.execute(
                "INSERT INTO dendritic_runtime_routes(tenant_id,scope_id,revision,pack_digest,active,payload_json) "
                "VALUES(?,?,?,?,?,?)",
                (tenant, scope, value["revision"], value["pack_digest"], 0, canonical_json(value)),
            )
            self._record_idempotency(connection, tenant, key_digest, value)
            self._record_audit(connection, value, action="rollback", scope=scope)
        return value

    def delete(
        self,
        *,
        tenant_id: str,
        pack_digest: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create an irreversible registry tombstone after dependency checks."""
        current = self.get(tenant_id=tenant_id, pack_digest=pack_digest)
        if current["state"] not in {"rejected", "revoked"}:
            raise DendriticMemoryRegistryConflict("dendritic_pack_delete_state_conflict")
        if self._is_active_or_parent(tenant_id=tenant_id, pack_digest=pack_digest):
            raise DendriticMemoryRegistryConflict("dendritic_pack_delete_dependency_conflict")
        return self._append(
            current,
            state="deleted",
            reason_code="dendritic_pack_deleted_by_retention_policy",
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            extra={"artifact_ref": None, "deleted_at": _now()},
            action="delete",
        )

    def list_routes(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        if not 1 <= limit <= 100:
            raise ValueError("dendritic_route_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM dendritic_runtime_routes candidate WHERE tenant_id=? "
                "AND revision=(SELECT MAX(revision) FROM dendritic_runtime_routes "
                "WHERE tenant_id=candidate.tenant_id AND scope_id=candidate.scope_id) "
                "ORDER BY scope_id LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def audit(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        if not 1 <= limit <= 500:
            raise ValueError("dendritic_audit_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM dendritic_registry_audit WHERE tenant_id=? "
                "ORDER BY sequence DESC LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def get(self, *, tenant_id: str, pack_digest: str) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        digest = require_digest(pack_digest, "pack_digest")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM dendritic_pack_revisions WHERE tenant_id=? AND pack_digest=? "
                "ORDER BY revision DESC LIMIT 1",
                (tenant, digest),
            ).fetchone()
        if not row:
            raise KeyError("dendritic_pack_not_found")
        return json.loads(row[0])

    def list(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        if not 1 <= limit <= 100:
            raise ValueError("dendritic_pack_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM dendritic_pack_revisions candidate WHERE tenant_id=? "
                "AND revision=(SELECT MAX(revision) FROM dendritic_pack_revisions "
                "WHERE tenant_id=candidate.tenant_id AND pack_digest=candidate.pack_digest) "
                "ORDER BY pack_digest LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def _create(
        self,
        manifest: DendriticMemoryPackManifestV1,
        *,
        artifact_ref: str,
        state: str,
        reason_code: str,
        idempotency_key: str,
        action: str,
    ) -> dict[str, Any]:
        key_digest = self._idempotency(manifest.tenant_id, idempotency_key)
        with self._transaction, self._connect() as connection:
            replay = self._replay(connection, manifest.tenant_id, key_digest)
            if replay:
                return replay
            existing = connection.execute(
                "SELECT 1 FROM dendritic_pack_revisions WHERE tenant_id=? AND pack_digest=?",
                (manifest.tenant_id, manifest.digest),
            ).fetchone()
            if existing:
                raise DendriticMemoryRegistryConflict("dendritic_pack_already_registered")
            value = {
                "tenant_id": manifest.tenant_id,
                "pack_digest": manifest.digest,
                "manifest": manifest.to_dict(),
                "artifact_ref": artifact_ref,
                "state": state,
                "revision": 1,
                "reason_code": reason_code,
                "updated_at": _now(),
                "experimental": True,
                "production_eligible": False,
                "claims_verified": False,
            }
            connection.execute(
                "INSERT INTO dendritic_pack_revisions(tenant_id,pack_digest,revision,payload_json) VALUES(?,?,?,?)",
                (manifest.tenant_id, manifest.digest, 1, canonical_json(value)),
            )
            self._record_idempotency(connection, manifest.tenant_id, key_digest, value)
            self._record_audit(connection, value, action=action, scope="registry")
        return value

    def _append(
        self,
        current: Mapping[str, Any],
        *,
        state: str,
        reason_code: str,
        expected_revision: int,
        idempotency_key: str,
        extra: Mapping[str, Any] | None = None,
        action: str,
    ) -> dict[str, Any]:
        tenant = str(current["tenant_id"])
        key_digest = self._idempotency(tenant, idempotency_key)
        with self._transaction, self._connect() as connection:
            replay = self._replay(connection, tenant, key_digest)
            if replay:
                return replay
            latest = self.get(tenant_id=tenant, pack_digest=str(current["pack_digest"]))
            if latest["revision"] != expected_revision:
                raise DendriticMemoryRegistryConflict("dendritic_pack_revision_conflict")
            value = {
                **latest,
                **dict(extra or {}),
                "state": state,
                "revision": expected_revision + 1,
                "reason_code": reason_code,
                "updated_at": _now(),
            }
            connection.execute(
                "INSERT INTO dendritic_pack_revisions(tenant_id,pack_digest,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, value["pack_digest"], value["revision"], canonical_json(value)),
            )
            self._record_idempotency(connection, tenant, key_digest, value)
            self._record_audit(connection, value, action=action, scope="registry")
        return value

    def _is_active_or_parent(self, *, tenant_id: str, pack_digest: str) -> bool:
        with self._connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM dendritic_runtime_routes candidate WHERE tenant_id=? AND pack_digest=? AND active=1 "
                "AND revision=(SELECT MAX(revision) FROM dendritic_runtime_routes "
                "WHERE tenant_id=candidate.tenant_id AND scope_id=candidate.scope_id) LIMIT 1",
                (tenant_id, pack_digest),
            ).fetchone()
            rows = connection.execute(
                "SELECT payload_json FROM dendritic_pack_revisions candidate WHERE tenant_id=? "
                "AND revision=(SELECT MAX(revision) FROM dendritic_pack_revisions "
                "WHERE tenant_id=candidate.tenant_id AND pack_digest=candidate.pack_digest)",
                (tenant_id,),
            ).fetchall()
        if active:
            return True
        return any(
            pack_digest in (json.loads(row[0]).get("manifest", {}).get("parent_pack_digests") or [])
            and json.loads(row[0]).get("state") != "deleted"
            for row in rows
        )

    @staticmethod
    def _idempotency(tenant_id: str, key: str) -> str:
        normalized = str(key or "").strip()
        if not 8 <= len(normalized) <= 256 or any(character.isspace() for character in normalized):
            raise ValueError("dendritic_idempotency_key_invalid")
        return canonical_digest([tenant_id, normalized])

    @staticmethod
    def _replay(connection: sqlite3.Connection, tenant_id: str, key_digest: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT payload_json FROM dendritic_registry_idempotency WHERE tenant_id=? AND key_digest=?",
            (tenant_id, key_digest),
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _record_idempotency(
        connection: sqlite3.Connection, tenant_id: str, key_digest: str, payload: Mapping[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO dendritic_registry_idempotency(tenant_id,key_digest,payload_json) VALUES(?,?,?)",
            (tenant_id, key_digest, canonical_json(payload)),
        )

    @staticmethod
    def _record_audit(
        connection: sqlite3.Connection,
        payload: Mapping[str, Any],
        *,
        action: str,
        scope: str,
    ) -> None:
        event = {
            "schema": "ananta.dendritic-memory-audit.v1",
            "tenant_id": payload["tenant_id"],
            "action": action,
            "policy_actor": "automatic-policy",
            "scope": scope,
            "reason_code": payload["reason_code"],
            "pack_digest": payload.get("pack_digest"),
            "artifact_hash": payload.get("pack_digest"),
            "report_id": payload.get("evaluation_digest"),
            "recorded_at": _now(),
            "human_intervention_required": False,
        }
        connection.execute(
            "INSERT INTO dendritic_registry_audit(tenant_id,payload_json) VALUES(?,?)",
            (payload["tenant_id"], canonical_json(event)),
        )

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            upgrade_registry_store(connection)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DendriticMemoryRegistryConflict", "DendriticMemoryRegistryService"]
