"""Shared Hub infrastructure for vector-index task lifecycle services."""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

from agent.services.vector_index_preparation_policy import (
    VectorIndexPreparationPolicyPort,
    build_vector_index_preparation_policy,
)
from agent.services.vector_index_task_contracts import (
    VECTOR_INDEX_TASK_SCHEMA,
    VectorIndexTrustedScope,
)
from agent.services.vector_index_worker_result_boundary import (
    VectorIndexWorkerResultBoundary,
)
from agent.services.vector_store_rollout_service import (
    VectorStoreRolloutService,
    get_vector_store_rollout_service,
)
from ananta_contracts.vector_index_dispatch import (
    canonicalize_vector_index_worker_audience,
)

ACTIVE_STATUSES = frozenset(
    {
        "created",
        "todo",
        "blocked",
        "blocked_by_dependency",
        "assigned",
        "in_progress",
        "running",
    }
)
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
COMPATIBILITY_ACTIVATING_OPERATIONS = frozenset({"refresh", "rebuild", "migrate"})
ROLLOUT_SOURCE_LAYERS = frozenset(
    {
        "global_json_default",
        "profile_override",
        "workspace_override",
    }
)
POLICY_DECISION = "worker_delegation_allowed"
DISPATCH_SCHEMA = "ananta.vector_index_task_dispatch.v1"
DISPATCH_ADMISSION_SCHEMA = "ananta.vector_index_task_dispatch_admission.v1"
PENDING_DISPATCH_AUDIENCE = "http://hub-control-plane.invalid"
ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
DISPATCH_PHASES = frozenset({"pending", "propose", "execute"})


def normalize_vector_index_dispatch_audience(value: Any) -> str:
    """Return one canonical HTTP(S) Worker origin."""

    return canonicalize_vector_index_worker_audience(value)


class VectorIndexTaskQueuePort(Protocol):
    """Minimal Hub queue capability required by the lifecycle service."""

    def ingest_task(self, **kwargs: Any) -> None: ...


class VectorIndexTaskRepositoryPort(Protocol):
    """Read-only task repository capability required by the service."""

    def get_by_id(self, task_id: str) -> Any: ...

    def get_all(self) -> list[Any]: ...


class VectorIndexTaskSignerPort(Protocol):
    """Minimal Hub-only capability; verification is intentionally absent."""

    def attest(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...


class VectorIndexScopeFencePort(Protocol):
    """Cross-instance serialization capability for one trusted Vector scope."""

    def mutation_lock(
        self,
        task_id: str,
    ) -> AbstractContextManager[bool]: ...


class VectorIndexTaskLifecycleSupport:
    """Own shared dependencies and atomic persistence helpers.

    Command, query, and dispatch services receive this object explicitly.  It
    keeps the infrastructure boundary small while ensuring all collaborators
    share the same lock, clock, repository, and compare-and-set implementation.
    """

    def __init__(
        self,
        *,
        task_queue: VectorIndexTaskQueuePort | None = None,
        task_repository: VectorIndexTaskRepositoryPort | None = None,
        rollout_service: VectorStoreRolloutService | None = None,
        status_updater: Callable[..., Any] | None = None,
        status_cas_updater: Callable[..., bool] | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        result_boundary: VectorIndexWorkerResultBoundary | None = None,
        preparation_policy: VectorIndexPreparationPolicyPort | None = None,
        task_signer: VectorIndexTaskSignerPort | None = None,
        scope_fence: VectorIndexScopeFencePort | None = None,
        attempt_id_factory: Callable[[], str] | None = None,
        dispatch_ttl_seconds: float | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._task_repository = task_repository
        self._rollout = rollout_service or get_vector_store_rollout_service()
        self._status_updater = status_updater
        self._status_cas_updater = status_cas_updater
        self._audit = audit or self._default_audit
        self._clock = clock
        self._result_boundary = result_boundary or VectorIndexWorkerResultBoundary()
        self._preparation_policy = (
            preparation_policy if preparation_policy is not None else build_vector_index_preparation_policy()
        )
        self._task_signer = task_signer
        self._scope_fence = scope_fence
        self._attempt_id_factory = (
            attempt_id_factory if attempt_id_factory is not None else lambda: secrets.token_urlsafe(24)
        )
        self._dispatch_ttl_seconds = self._validate_dispatch_ttl(dispatch_ttl_seconds)
        self._lock = threading.RLock()

    @staticmethod
    def _validate_dispatch_ttl(value: float | None) -> float:
        raw = (
            value
            if value is not None
            else os.environ.get(
                "ANANTA_VECTOR_INDEX_TASK_DISPATCH_TTL_SECONDS",
                "300",
            )
        )
        try:
            ttl = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("vector_index_task_dispatch_ttl_invalid") from exc
        if not 30.0 <= ttl <= 3_600.0:
            raise ValueError("vector_index_task_dispatch_ttl_invalid")
        return ttl

    def _signer(self) -> VectorIndexTaskSignerPort:
        if self._task_signer is None:
            from agent.services.vector_index_task_attestation_service import (
                load_vector_index_task_signer,
            )

            self._task_signer = load_vector_index_task_signer()
        return self._task_signer

    @staticmethod
    def _default_audit(event: str, payload: dict[str, Any]) -> None:
        from agent.common.audit import log_audit

        log_audit(event, payload)

    def _queue(self) -> VectorIndexTaskQueuePort:
        if self._task_queue is not None:
            return self._task_queue
        from agent.services.task_queue_service import get_task_queue_service

        return get_task_queue_service()

    def _repository(self) -> VectorIndexTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def _scope_fence_port(self) -> VectorIndexScopeFencePort:
        if self._scope_fence is None:
            from agent.services.task_mutation_lock_service import (
                get_task_mutation_lock_port,
            )

            self._scope_fence = get_task_mutation_lock_port()
        return self._scope_fence

    def _scope_mutation_lock(
        self,
        scope_fingerprint: str,
    ) -> AbstractContextManager[bool]:
        fingerprint = str(scope_fingerprint or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise ValueError("vector_index_scope_fingerprint_invalid")
        return self._scope_fence_port().mutation_lock(f"vector-index-scope:{fingerprint}")

    def _update_status(self, task_id: str, status: str, **kwargs: Any) -> Any:
        if self._status_updater is not None:
            return self._status_updater(task_id, status, **kwargs)
        from agent.services.task_runtime_service import update_local_task_status

        return update_local_task_status(task_id, status, **kwargs)

    def _compare_and_set_status(
        self,
        task_id: str,
        status: str,
        *,
        expected_statuses: set[str],
        authoritative_predicate: Callable[[Any], bool],
        **kwargs: Any,
    ) -> bool:
        """Commit one lifecycle mutation against the authoritative Hub row."""

        if self._status_cas_updater is not None:
            return bool(
                self._status_cas_updater(
                    task_id,
                    status,
                    expected_statuses=expected_statuses,
                    authoritative_predicate=authoritative_predicate,
                    **kwargs,
                )
            )
        if self._status_updater is not None:
            current = self._repository().get_by_id(str(task_id))
            raw = self._raw(current)
            if str(raw.get("status") or "").strip().lower() not in expected_statuses or not authoritative_predicate(
                current
            ):
                return False
            self._status_updater(task_id, status, **kwargs)
            return True
        from agent.services.task_runtime_service import (
            compare_and_set_local_task_status,
        )

        return compare_and_set_local_task_status(
            task_id,
            status,
            expected_statuses=expected_statuses,
            authoritative_predicate=authoritative_predicate,
            **kwargs,
        )

    @staticmethod
    def _raw(task: Any) -> dict[str, Any]:
        if task is None:
            return {}
        if hasattr(task, "model_dump"):
            return dict(task.model_dump())
        return dict(task)

    @staticmethod
    def _envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
        context = raw.get("worker_execution_context")
        if not isinstance(context, Mapping):
            return {}
        envelope = context.get("vector_index_task")
        return dict(envelope) if isinstance(envelope, Mapping) else {}

    def _authoritative_task_matches(
        self,
        task: Any,
        *,
        job_id: str,
        request_fingerprint: str,
        attempt_id: str,
        worker_audience: str | None = None,
    ) -> bool:
        raw = self._raw(task)
        envelope = self._envelope(raw)
        dispatch = dict(envelope.get("dispatch") or {})
        if (
            str(raw.get("id") or "") != str(job_id)
            or str(raw.get("task_kind") or "").strip().lower() != "vector_index_operation"
            or envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA
            or str(envelope.get("job_id") or "") != str(job_id)
            or str(envelope.get("request_fingerprint") or "") != str(request_fingerprint)
            or str(dispatch.get("attempt_id") or "") != str(attempt_id)
        ):
            return False
        if worker_audience is None:
            return True
        try:
            assigned_audience = self._normalize_dispatch_audience(raw.get("assigned_agent_url"))
        except ValueError:
            return False
        return assigned_audience == worker_audience

    @staticmethod
    def _finite_timestamp(value: Any, *, fallback: Any) -> float:
        for candidate in (value, fallback, 0.0):
            try:
                normalized = float(candidate)
            except (TypeError, ValueError):
                continue
            if normalized == normalized and abs(normalized) != float("inf"):
                return normalized
        return 0.0

    def _new_dispatch(
        self,
        *,
        previous: Any,
        audience: str,
        phase: str,
        issued_at: float,
    ) -> dict[str, Any]:
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase not in DISPATCH_PHASES:
            raise ValueError("vector_index_task_dispatch_phase_invalid")
        normalized_audience = self._normalize_dispatch_audience(audience)
        attempt_id = str(self._attempt_id_factory() or "").strip()
        if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
            raise RuntimeError("vector_index_task_attempt_id_invalid")
        if not math.isfinite(float(issued_at)) or float(issued_at) < 0:
            raise RuntimeError("vector_index_task_dispatch_time_invalid")
        prior = dict(previous) if isinstance(previous, Mapping) else {}
        prior_sequence = prior.get("sequence", -1)
        if isinstance(prior_sequence, bool) or not isinstance(prior_sequence, int) or prior_sequence < 0:
            prior_sequence = -1
        return {
            "schema": DISPATCH_SCHEMA,
            "attempt_id": attempt_id,
            "sequence": prior_sequence + 1,
            "audience": normalized_audience,
            "phase": normalized_phase,
            "issued_at": float(issued_at),
            "expires_at": float(issued_at) + self._dispatch_ttl_seconds,
        }

    @staticmethod
    def _normalize_dispatch_audience(value: Any) -> str:
        return normalize_vector_index_dispatch_audience(value)

    def _assert_scope_available(
        self,
        scope: VectorIndexTrustedScope,
        *,
        exclude_job_id: str | None = None,
    ) -> None:
        fingerprint = scope.fingerprint()
        for task in self._repository().get_all():
            raw = self._raw(task)
            if str(raw.get("id") or "") == str(exclude_job_id or ""):
                continue
            envelope = self._envelope(raw)
            if envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA:
                continue
            if envelope.get("scope_fingerprint") != fingerprint:
                continue
            status = str(raw.get("status") or "todo").strip().lower()
            if status in ACTIVE_STATUSES:
                raise RuntimeError("vector_index_task_conflict")

    def _audit_task(
        self,
        action: str,
        envelope: Mapping[str, Any],
        actor: str,
    ) -> None:
        resolved = dict(envelope.get("resolved_config") or {})
        self._audit(
            f"vector_index_task_{action}",
            {
                "actor": str(actor or "unknown"),
                "job_id": str(envelope.get("job_id") or ""),
                "operation": str(envelope.get("operation") or ""),
                "scope_fingerprint": str(envelope.get("scope_fingerprint") or ""),
                "idempotency_key_hash": hashlib.sha256(
                    str(envelope.get("idempotency_key") or "").encode("utf-8")
                ).hexdigest(),
                "provider": str(resolved.get("provider") or ""),
                "resolved_config_hash": str(resolved.get("config_hash") or ""),
                "policy_decision": str(envelope.get("policy_decision") or ""),
                "source_layers": [
                    str(layer)
                    for layer in list(envelope.get("policy_source_layers") or [])
                    if str(layer) in ROLLOUT_SOURCE_LAYERS
                ],
            },
        )


__all__ = [
    "ACTIVE_STATUSES",
    "ATTEMPT_ID_PATTERN",
    "COMPATIBILITY_ACTIVATING_OPERATIONS",
    "DISPATCH_ADMISSION_SCHEMA",
    "PENDING_DISPATCH_AUDIENCE",
    "POLICY_DECISION",
    "ROLLOUT_SOURCE_LAYERS",
    "TERMINAL_STATUSES",
    "VectorIndexScopeFencePort",
    "VectorIndexTaskLifecycleSupport",
    "VectorIndexTaskQueuePort",
    "VectorIndexTaskRepositoryPort",
    "VectorIndexTaskSignerPort",
    "normalize_vector_index_dispatch_audience",
]
