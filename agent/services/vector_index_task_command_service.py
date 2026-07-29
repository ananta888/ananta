"""Submission and administrative commands for vector-index tasks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agent.services.vector_index_task_contracts import (
    VECTOR_INDEX_OPERATIONS,
    VECTOR_INDEX_RESULT_SCHEMA,
    VECTOR_INDEX_TASK_SCHEMA,
    VectorIndexOperationPayload,
    VectorIndexTrustedScope,
    canonical_json,
    clone_json,
    validate_idempotency_key,
)
from agent.services.vector_index_task_lifecycle_support import (
    ACTIVE_STATUSES,
    PENDING_DISPATCH_AUDIENCE,
    POLICY_DECISION,
    ROLLOUT_SOURCE_LAYERS,
    TERMINAL_STATUSES,
    VectorIndexTaskLifecycleSupport,
)
from agent.services.vector_index_task_query_service import (
    VectorIndexTaskQueryService,
)
from worker.retrieval.vector_index_input_loader import (
    VectorIndexInputError,
    VectorIndexInputReference,
)
from worker.retrieval.vector_index_preparation import (
    VectorIndexPreparationSpec,
)


class VectorIndexTaskCommandService:
    """Mutate Hub-owned lifecycle state before Worker dispatch."""

    def __init__(
        self,
        support: VectorIndexTaskLifecycleSupport,
        queries: VectorIndexTaskQueryService,
    ) -> None:
        self._support = support
        self._queries = queries

    def submit(
        self,
        *,
        operation: str,
        trusted_scope: VectorIndexTrustedScope,
        idempotency_key: str,
        payload: Mapping[str, Any] | None,
        actor: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        normalized_operation = str(operation or "").strip().lower()
        if normalized_operation not in VECTOR_INDEX_OPERATIONS:
            raise ValueError("vector_index_operation_invalid")
        if normalized_operation == "search":
            raise ValueError("vector_index_search_must_not_enqueue")
        key = validate_idempotency_key(idempotency_key)
        policy_payload = dict(payload or {})
        self._validate_raw_input_ref_binding(
            policy_payload.get("input_ref"),
            scope=trusted_scope,
        )
        raw_preparation = policy_payload.get("preparation")
        raw_embedding = raw_preparation.get("embedding") if isinstance(raw_preparation, Mapping) else None
        requested_provider = (
            str(raw_embedding.get("provider") or "").strip().lower() if isinstance(raw_embedding, Mapping) else ""
        )
        if requested_provider in {"openai", "openai_compatible"}:
            canonical_preparation = self._support._preparation_policy.authorize(
                preparation=raw_preparation,
                trusted_domain=trusted_scope.domain,
            )
            if canonical_preparation is not None:
                policy_payload["preparation"] = canonical_preparation
        normalized_payload = VectorIndexOperationPayload.from_mapping(
            normalized_operation,
            policy_payload,
        )
        self._validate_input_ref_binding(
            normalized_payload,
            scope=trusted_scope,
        )
        normalized_payload = self._authorize_preparation(
            normalized_payload,
            operation=normalized_operation,
            scope=trusted_scope,
        )
        if normalized_payload.preparation is not None:
            VectorIndexPreparationSpec.from_mapping(normalized_payload.preparation).validate_scope_domain(
                trusted_scope.domain
            )
        self._validate_checkpoint_binding(
            normalized_payload,
            scope=trusted_scope,
            idempotency_key=key,
        )
        normalized_priority = str(priority or "medium").strip().lower()
        if normalized_priority not in {
            "low",
            "medium",
            "high",
            "critical",
        }:
            raise ValueError("vector_index_priority_invalid")
        request_intent = {
            "operation": normalized_operation,
            "scope": trusted_scope.to_dict(),
            "idempotency_key": key,
            "payload": normalized_payload.to_dict(),
        }
        request_fingerprint = hashlib.sha256(canonical_json(request_intent)).hexdigest()
        scope_fingerprint = trusted_scope.fingerprint()
        job_seed = {
            "scope_fingerprint": scope_fingerprint,
            "idempotency_key": key,
        }
        job_id = "vector-index-" + hashlib.sha256(canonical_json(job_seed)).hexdigest()[:32]
        with self._support._lock:
            with self._support._scope_mutation_lock(scope_fingerprint) as scope_fence_acquired:
                if not scope_fence_acquired:
                    raise RuntimeError("vector_index_scope_fence_unavailable")
                existing = self._queries.get_task(job_id)
                if existing is not None:
                    if existing.get("request_fingerprint") != request_fingerprint:
                        raise RuntimeError("vector_index_idempotency_mismatch")
                    return existing
                self._support._assert_scope_available(trusted_scope)
                resolved = self._support._rollout.resolve(
                    domain=trusted_scope.domain,
                    workspace_id=trusted_scope.workspace_id,
                    profile_name=trusted_scope.profile_name,
                )
                source_layers = tuple(resolved.source_layers)
                if not source_layers or any(layer not in ROLLOUT_SOURCE_LAYERS for layer in source_layers):
                    raise RuntimeError("vector_index_rollout_source_layers_invalid")
                now = float(self._support._clock())
                dispatch = self._support._new_dispatch(
                    previous=None,
                    audience=PENDING_DISPATCH_AUDIENCE,
                    phase="pending",
                    issued_at=now,
                )
                envelope = self._support._signer().attest(
                    {
                        "schema": VECTOR_INDEX_TASK_SCHEMA,
                        "job_id": job_id,
                        "operation": normalized_operation,
                        "scope": trusted_scope.to_dict(),
                        "scope_fingerprint": scope_fingerprint,
                        "idempotency_key": key,
                        "request_fingerprint": request_fingerprint,
                        "resolved_config": resolved.to_worker_payload(),
                        "policy_decision": POLICY_DECISION,
                        "policy_source_layers": list(source_layers),
                        "payload": normalized_payload.to_dict(),
                        "created_by": str(actor or "unknown"),
                        "created_at": now,
                        "dispatch": dispatch,
                    }
                )
                self._support._queue().ingest_task(
                    task_id=job_id,
                    status="todo",
                    title=(
                        f"Vector index {normalized_operation}: {trusted_scope.domain}/{trusted_scope.repository_id}"
                    )[:200],
                    description=(
                        "Hub-owned, worker-delegated vector index mutation. Search is intentionally excluded."
                    ),
                    priority=normalized_priority,
                    created_by=str(actor or "vector-index-api"),
                    source="vector_index",
                    tags=[
                        "vector_index",
                        "hub_delegated",
                        "persistent_job",
                        normalized_operation,
                    ],
                    event_type="task_ingested",
                    event_channel="hub_task_queue",
                    event_details={
                        "operation": normalized_operation,
                        "scope_fingerprint": scope_fingerprint,
                        "request_fingerprint": request_fingerprint,
                        "domain_event_type": ("vector_index_task_queued"),
                        "policy_decision": POLICY_DECISION,
                        "source_layers": list(source_layers),
                        "resolved_config_hash": resolved.config_hash,
                    },
                    extra_fields={
                        "task_kind": "vector_index_operation",
                        "retrieval_intent": (f"vector_{normalized_operation}"),
                        "required_context_scope": trusted_scope.domain,
                        "required_capabilities": [
                            "retrieval",
                            "index_write",
                            "vector_index_operation",
                        ],
                        "worker_execution_context": {"vector_index_task": envelope},
                        "verification_spec": {
                            "schema": VECTOR_INDEX_RESULT_SCHEMA,
                            "idempotency_key": key,
                        },
                    },
                )
                created = self._queries.get_task(job_id)
                if created is None:
                    raise RuntimeError("vector_index_task_persistence_failed")
                self._support._audit_task("queued", envelope, actor)
                return created

    def cancel(self, *, job_id: str, actor: str) -> dict[str, Any]:
        with self._support._lock:
            for _attempt in range(4):
                raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
                envelope = self._support._envelope(raw)
                if not envelope:
                    raise ValueError("vector_index_task_not_found")
                status = str(raw.get("status") or "").strip().lower()
                if status in TERMINAL_STATUSES:
                    return self._queries.get_task(job_id) or {}
                if status not in ACTIVE_STATUSES:
                    raise RuntimeError("vector_index_cancel_state_invalid")
                dispatch = dict(envelope.get("dispatch") or {})
                attempt_id = str(dispatch.get("attempt_id") or "")
                request_fingerprint = str(envelope.get("request_fingerprint") or "")
                committed = self._support._compare_and_set_status(
                    str(job_id),
                    "cancelled",
                    expected_statuses={status},
                    authoritative_predicate=lambda task: self._support._authoritative_task_matches(
                        task,
                        job_id=str(job_id),
                        request_fingerprint=request_fingerprint,
                        attempt_id=attempt_id,
                    ),
                    status_reason_code=("vector_index_cancelled_by_hub"),
                    event_type="vector_index_task_cancelled",
                    event_actor=str(actor or "unknown"),
                    event_details={
                        "scope_fingerprint": envelope.get("scope_fingerprint"),
                        "idempotency_key_hash": hashlib.sha256(
                            str(envelope.get("idempotency_key") or "").encode("utf-8")
                        ).hexdigest(),
                    },
                )
                if committed:
                    self._support._audit_task(
                        "cancelled",
                        envelope,
                        actor,
                    )
                    return self._queries.get_task(job_id) or {}
            raise RuntimeError("vector_index_task_cas_conflict")

    def retry(self, *, job_id: str, actor: str) -> dict[str, Any]:
        with self._support._lock:
            initial_raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
            initial_envelope = self._support._envelope(initial_raw)
            if not initial_envelope:
                raise ValueError("vector_index_task_not_found")
            initial_scope = VectorIndexTrustedScope(**dict(initial_envelope.get("scope") or {}))
            scope_fingerprint = initial_scope.fingerprint()
            with self._support._scope_mutation_lock(scope_fingerprint) as scope_fence_acquired:
                if not scope_fence_acquired:
                    raise RuntimeError("vector_index_scope_fence_unavailable")
                raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
                envelope = self._support._envelope(raw)
                if not envelope:
                    raise ValueError("vector_index_task_not_found")
                scope = VectorIndexTrustedScope(**dict(envelope.get("scope") or {}))
                if scope.fingerprint() != scope_fingerprint:
                    raise RuntimeError("vector_index_task_scope_conflict")
                status = str(raw.get("status") or "").strip().lower()
                if status not in {"failed", "cancelled"}:
                    raise RuntimeError("vector_index_retry_state_invalid")
                self._support._assert_scope_available(
                    scope,
                    exclude_job_id=str(job_id),
                )
                resumed_envelope, verification = self._resume_envelope(
                    envelope,
                    raw=raw,
                    scope=scope,
                )
                resumed_payload = VectorIndexOperationPayload.from_mapping(
                    str(resumed_envelope.get("operation") or ""),
                    dict(resumed_envelope.get("payload") or {}),
                )
                self._validate_input_ref_binding(
                    resumed_payload,
                    scope=scope,
                )
                resumed_payload = self._authorize_preparation(
                    resumed_payload,
                    operation=str(resumed_envelope.get("operation") or ""),
                    scope=scope,
                )
                resumed_envelope["payload"] = resumed_payload.to_dict()
                resumed_envelope["dispatch"] = self._support._new_dispatch(
                    previous=resumed_envelope.get("dispatch"),
                    audience=PENDING_DISPATCH_AUDIENCE,
                    phase="pending",
                    issued_at=float(self._support._clock()),
                )
                resumed_envelope = self._support._signer().attest(resumed_envelope)
                current_dispatch = dict(envelope.get("dispatch") or {})
                committed = self._support._compare_and_set_status(
                    str(job_id),
                    "todo",
                    expected_statuses={status},
                    authoritative_predicate=lambda task: self._support._authoritative_task_matches(
                        task,
                        job_id=str(job_id),
                        request_fingerprint=str(envelope.get("request_fingerprint") or ""),
                        attempt_id=str(current_dispatch.get("attempt_id") or ""),
                    ),
                    status_reason_code=None,
                    error=None,
                    force=True,
                    worker_execution_context={"vector_index_task": resumed_envelope},
                    verification_status=verification,
                    event_type="vector_index_task_retried",
                    event_actor=str(actor or "unknown"),
                    event_details={
                        "scope_fingerprint": envelope.get("scope_fingerprint"),
                        "same_idempotency_key": True,
                    },
                )
                if not committed:
                    raise RuntimeError("vector_index_task_cas_conflict")
                self._support._audit_task(
                    "retried",
                    envelope,
                    actor,
                )
                return self._queries.get_task(job_id) or {}

    @staticmethod
    def _validate_input_ref_binding(
        payload: VectorIndexOperationPayload,
        *,
        scope: VectorIndexTrustedScope,
    ) -> None:
        VectorIndexTaskCommandService._validate_raw_input_ref_binding(
            payload.input_ref,
            scope=scope,
        )

    @staticmethod
    def _validate_raw_input_ref_binding(
        input_ref: Any,
        *,
        scope: VectorIndexTrustedScope,
    ) -> None:
        if input_ref is None:
            return
        if not isinstance(input_ref, Mapping):
            raise ValueError("vector_index_input_ref_invalid")
        try:
            reference = VectorIndexInputReference.from_mapping(
                input_ref,
                require_sha256=True,
                require_scope_fingerprint=True,
            )
            reference.validate_binding(scope)
        except VectorIndexInputError as exc:
            raise ValueError(exc.reason) from exc

    def _authorize_preparation(
        self,
        payload: VectorIndexOperationPayload,
        *,
        operation: str,
        scope: VectorIndexTrustedScope,
    ) -> VectorIndexOperationPayload:
        canonical = self._support._preparation_policy.authorize(
            preparation=payload.preparation,
            trusted_domain=scope.domain,
        )
        if canonical is None:
            return payload
        candidate = payload.to_dict()
        candidate["preparation"] = canonical
        return VectorIndexOperationPayload.from_mapping(
            operation,
            candidate,
        )

    @staticmethod
    def _validate_checkpoint_binding(
        payload: VectorIndexOperationPayload,
        *,
        scope: VectorIndexTrustedScope,
        idempotency_key: str,
    ) -> None:
        migration = dict(payload.migration or {})
        checkpoint = migration.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            return
        expected_key_hash = hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
        if (
            checkpoint.get("scope_fingerprint") != scope.fingerprint()
            or checkpoint.get("idempotency_key_hash") != expected_key_hash
        ):
            raise ValueError("vector_index_migration_checkpoint_binding_invalid")

    def _resume_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        raw: Mapping[str, Any],
        scope: VectorIndexTrustedScope,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resumed = clone_json(dict(envelope))
        verification = clone_json(dict(raw.get("verification_status") or {}))
        previous = verification.pop("vector_index_task_result", None)
        verification.pop("vector_index_dispatch_admission", None)
        if isinstance(previous, Mapping):
            verification["vector_index_previous_attempt"] = clone_json(dict(previous))
        if str(resumed.get("operation") or "") != "migrate":
            return resumed, verification
        result = dict(previous.get("result") or {}) if isinstance(previous, Mapping) else {}
        checkpoint = result.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            return resumed, verification
        payload = dict(resumed.get("payload") or {})
        migration = dict(payload.get("migration") or {})
        migration["checkpoint"] = dict(checkpoint)
        normalized = VectorIndexOperationPayload.from_mapping(
            "migrate",
            {**payload, "migration": migration},
        )
        self._validate_input_ref_binding(normalized, scope=scope)
        self._validate_checkpoint_binding(
            normalized,
            scope=scope,
            idempotency_key=str(resumed.get("idempotency_key") or ""),
        )
        resumed["payload"] = normalized.to_dict()
        return resumed, verification


__all__ = ["VectorIndexTaskCommandService"]
