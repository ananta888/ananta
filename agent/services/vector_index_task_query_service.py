"""Read projections for Hub-owned vector-index lifecycle tasks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agent.services.vector_index_task_contracts import (
    VECTOR_INDEX_TASK_SCHEMA,
    VectorIndexTrustedScope,
    canonical_json,
    clone_json,
)
from agent.services.vector_index_task_lifecycle_support import (
    COMPATIBILITY_ACTIVATING_OPERATIONS,
    VectorIndexTaskLifecycleSupport,
)


class VectorIndexTaskQueryService:
    """Build stable task and compatibility read models."""

    def __init__(self, support: VectorIndexTaskLifecycleSupport) -> None:
        self._support = support

    def get_task(self, job_id: str) -> dict[str, Any] | None:
        raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
        envelope = self._support._envelope(raw)
        if envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA:
            return None
        task_status = str(raw.get("status") or "todo").strip().lower()
        status = {
            "created": "queued",
            "todo": "queued",
            "blocked": "queued",
            "blocked_by_dependency": "queued",
            "assigned": "running",
            "in_progress": "running",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(task_status, "queued")
        payload = dict(envelope.get("payload") or {})
        verification = dict(raw.get("verification_status") or {})
        result = verification.get("vector_index_task_result")
        view = {
            "job_id": envelope.get("job_id"),
            "operation": envelope.get("operation"),
            "scope": clone_json(envelope.get("scope") or {}),
            "scope_fingerprint": envelope.get("scope_fingerprint"),
            "idempotency_key": envelope.get("idempotency_key"),
            "request_fingerprint": envelope.get("request_fingerprint"),
            "resolved_config_hash": (dict(envelope.get("resolved_config") or {}).get("config_hash")),
            "provider": dict(envelope.get("resolved_config") or {}).get("provider"),
            "policy_decision": envelope.get("policy_decision"),
            "source_layers": clone_json(list(envelope.get("policy_source_layers") or [])),
            "status": status,
            "created_by": envelope.get("created_by"),
            "created_at": envelope.get("created_at"),
            "attempt_id": dict(envelope.get("dispatch") or {}).get("attempt_id"),
            "dispatch_sequence": dict(envelope.get("dispatch") or {}).get("sequence"),
            "dispatch_phase": dict(envelope.get("dispatch") or {}).get("phase"),
            "dispatch_audience": dict(envelope.get("dispatch") or {}).get("audience"),
            "priority": raw.get("priority"),
            "payload_summary": {
                "point_count": len(list(payload.get("points") or [])),
                "point_id_count": len(list(payload.get("point_ids") or [])),
                "has_input_ref": isinstance(
                    payload.get("input_ref"),
                    Mapping,
                ),
                "preparation_kind": (str(dict(payload.get("preparation") or {}).get("kind") or "") or None),
                "batch_size": payload.get("batch_size"),
                "dry_run": bool(
                    dict(payload.get("migration") or {}).get(
                        "dry_run",
                        False,
                    )
                ),
            },
        }
        if isinstance(result, Mapping):
            view["result"] = clone_json(dict(result))
        return {key: value for key, value in view.items() if value is not None}

    def get_latest_completed_compatibility_state(
        self,
        *,
        trusted_scope: VectorIndexTrustedScope,
    ) -> dict[str, Any] | None:
        """Return the last Hub-accepted compatibility activation for a scope."""

        candidates: list[tuple[tuple[float, float, str], dict[str, Any]]] = []
        scope_fingerprint = trusted_scope.fingerprint()
        for task in self._support._repository().get_all():
            raw = self._support._raw(task)
            if str(raw.get("status") or "").strip().lower() != "completed":
                continue
            envelope = self._support._envelope(raw)
            if (
                envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA
                or envelope.get("scope_fingerprint") != scope_fingerprint
            ):
                continue
            operation = str(envelope.get("operation") or "").strip().lower()
            if operation not in COMPATIBILITY_ACTIVATING_OPERATIONS:
                continue
            verification = raw.get("verification_status") or {}
            if not isinstance(verification, Mapping):
                continue
            accepted = verification.get("vector_index_task_result")
            if not isinstance(accepted, Mapping) or str(accepted.get("status") or "").strip().lower() != "completed":
                continue
            result = accepted.get("result") or {}
            if not isinstance(result, Mapping):
                continue
            payload = envelope.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if operation == "migrate":
                migration = payload.get("migration") or {}
                if (
                    not isinstance(migration, Mapping)
                    or bool(migration.get("dry_run", False))
                    or result.get("activated") is not True
                ):
                    continue
            compatibility = payload.get("compatibility")
            if not isinstance(compatibility, Mapping):
                continue
            preparation = payload.get("preparation") or {}
            if not isinstance(preparation, Mapping):
                preparation = {}
            updated_at = self._support._finite_timestamp(
                raw.get("updated_at"),
                fallback=envelope.get("created_at"),
            )
            created_at = self._support._finite_timestamp(
                envelope.get("created_at"),
                fallback=0.0,
            )
            job_id = str(envelope.get("job_id") or "")
            state = {
                "job_id": job_id,
                "operation": operation,
                "scope_fingerprint": scope_fingerprint,
                "compatibility": clone_json(dict(compatibility)),
                "retrieval_cache_state": str(preparation.get("retrieval_cache_state") or ""),
                "completed_at": updated_at,
            }
            state["state_version"] = hashlib.sha256(canonical_json(state)).hexdigest()
            candidates.append(((updated_at, created_at, job_id), state))
        if not candidates:
            return None
        return clone_json(max(candidates, key=lambda item: item[0])[1])


__all__ = ["VectorIndexTaskQueryService"]
