"""Compatibility facade for Hub-owned vector-index task lifecycles.

The facade preserves the original API while delegating read projections,
commands, and Worker dispatch/result handling to focused services.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from agent.services.vector_index_preparation_policy import (
    VectorIndexPreparationPolicyPort,
)
from agent.services.vector_index_task_command_service import (
    VectorIndexTaskCommandService,
)
from agent.services.vector_index_task_contracts import (
    VectorIndexOperationPayload,
    VectorIndexTrustedScope,
)
from agent.services.vector_index_task_dispatch_service import (
    VectorIndexTaskDispatchService,
)
from agent.services.vector_index_task_lifecycle_support import (
    VectorIndexScopeFencePort,
    VectorIndexTaskLifecycleSupport,
    VectorIndexTaskQueuePort,
    VectorIndexTaskRepositoryPort,
    VectorIndexTaskSignerPort,
)
from agent.services.vector_index_task_lifecycle_support import (
    normalize_vector_index_dispatch_audience as _normalize_audience,
)
from agent.services.vector_index_task_query_service import (
    VectorIndexTaskQueryService,
)
from agent.services.vector_index_worker_result_boundary import (
    VectorIndexWorkerResultBoundary,
)
from agent.services.vector_store_rollout_service import (
    VectorStoreRolloutService,
)


def normalize_vector_index_dispatch_audience(value: Any) -> str:
    """Return one canonical HTTP(S) Worker origin."""

    return _normalize_audience(value)


class VectorIndexTaskService:
    """Coordinate focused Hub lifecycle services behind one stable facade."""

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
        self._support = VectorIndexTaskLifecycleSupport(
            task_queue=task_queue,
            task_repository=task_repository,
            rollout_service=rollout_service,
            status_updater=status_updater,
            status_cas_updater=status_cas_updater,
            audit=audit,
            clock=clock,
            result_boundary=result_boundary,
            preparation_policy=preparation_policy,
            task_signer=task_signer,
            scope_fence=scope_fence,
            attempt_id_factory=attempt_id_factory,
            dispatch_ttl_seconds=dispatch_ttl_seconds,
        )
        self._queries = VectorIndexTaskQueryService(self._support)
        self._commands = VectorIndexTaskCommandService(
            self._support,
            self._queries,
        )
        self._dispatch = VectorIndexTaskDispatchService(
            self._support,
            self._queries,
        )

    # Explicit dependency proxies preserve existing fixture and adapter seams.
    @property
    def _task_queue(self) -> VectorIndexTaskQueuePort | None:
        return self._support._task_queue

    @_task_queue.setter
    def _task_queue(
        self,
        value: VectorIndexTaskQueuePort | None,
    ) -> None:
        self._support._task_queue = value

    @property
    def _task_repository(
        self,
    ) -> VectorIndexTaskRepositoryPort | None:
        return self._support._task_repository

    @_task_repository.setter
    def _task_repository(
        self,
        value: VectorIndexTaskRepositoryPort | None,
    ) -> None:
        self._support._task_repository = value

    @property
    def _rollout(self) -> VectorStoreRolloutService:
        return self._support._rollout

    @_rollout.setter
    def _rollout(self, value: VectorStoreRolloutService) -> None:
        self._support._rollout = value

    @property
    def _status_updater(self) -> Callable[..., Any] | None:
        return self._support._status_updater

    @_status_updater.setter
    def _status_updater(
        self,
        value: Callable[..., Any] | None,
    ) -> None:
        self._support._status_updater = value

    @property
    def _status_cas_updater(self) -> Callable[..., bool] | None:
        return self._support._status_cas_updater

    @_status_cas_updater.setter
    def _status_cas_updater(
        self,
        value: Callable[..., bool] | None,
    ) -> None:
        self._support._status_cas_updater = value

    @property
    def _audit(self) -> Callable[[str, dict[str, Any]], None]:
        return self._support._audit

    @_audit.setter
    def _audit(
        self,
        value: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._support._audit = value

    @property
    def _clock(self) -> Callable[[], float]:
        return self._support._clock

    @_clock.setter
    def _clock(self, value: Callable[[], float]) -> None:
        self._support._clock = value

    @property
    def _result_boundary(self) -> VectorIndexWorkerResultBoundary:
        return self._support._result_boundary

    @_result_boundary.setter
    def _result_boundary(
        self,
        value: VectorIndexWorkerResultBoundary,
    ) -> None:
        self._support._result_boundary = value

    @property
    def _preparation_policy(self) -> VectorIndexPreparationPolicyPort:
        return self._support._preparation_policy

    @_preparation_policy.setter
    def _preparation_policy(
        self,
        value: VectorIndexPreparationPolicyPort,
    ) -> None:
        self._support._preparation_policy = value

    @property
    def _task_signer(self) -> VectorIndexTaskSignerPort | None:
        return self._support._task_signer

    @_task_signer.setter
    def _task_signer(
        self,
        value: VectorIndexTaskSignerPort | None,
    ) -> None:
        self._support._task_signer = value

    @property
    def _scope_fence(self) -> VectorIndexScopeFencePort | None:
        return self._support._scope_fence

    @_scope_fence.setter
    def _scope_fence(
        self,
        value: VectorIndexScopeFencePort | None,
    ) -> None:
        self._support._scope_fence = value

    @property
    def _attempt_id_factory(self) -> Callable[[], str]:
        return self._support._attempt_id_factory

    @_attempt_id_factory.setter
    def _attempt_id_factory(
        self,
        value: Callable[[], str],
    ) -> None:
        self._support._attempt_id_factory = value

    @property
    def _dispatch_ttl_seconds(self) -> float:
        return self._support._dispatch_ttl_seconds

    @_dispatch_ttl_seconds.setter
    def _dispatch_ttl_seconds(self, value: float) -> None:
        self._support._dispatch_ttl_seconds = value

    @property
    def _lock(self) -> Any:
        return self._support._lock

    @_lock.setter
    def _lock(self, value: Any) -> None:
        self._support._lock = value

    @staticmethod
    def _validate_dispatch_ttl(value: float | None) -> float:
        return VectorIndexTaskLifecycleSupport._validate_dispatch_ttl(value)

    def _signer(self) -> VectorIndexTaskSignerPort:
        return self._support._signer()

    @staticmethod
    def _default_audit(event: str, payload: dict[str, Any]) -> None:
        VectorIndexTaskLifecycleSupport._default_audit(event, payload)

    def _queue(self) -> VectorIndexTaskQueuePort:
        return self._support._queue()

    def _repository(self) -> VectorIndexTaskRepositoryPort:
        return self._support._repository()

    def _scope_fence_port(self) -> VectorIndexScopeFencePort:
        return self._support._scope_fence_port()

    def _scope_mutation_lock(
        self,
        scope_fingerprint: str,
    ) -> AbstractContextManager[bool]:
        return self._support._scope_mutation_lock(scope_fingerprint)

    def _update_status(
        self,
        task_id: str,
        status: str,
        **kwargs: Any,
    ) -> Any:
        return self._support._update_status(
            task_id,
            status,
            **kwargs,
        )

    def _compare_and_set_status(
        self,
        task_id: str,
        status: str,
        *,
        expected_statuses: set[str],
        authoritative_predicate: Callable[[Any], bool],
        **kwargs: Any,
    ) -> bool:
        return self._support._compare_and_set_status(
            task_id,
            status,
            expected_statuses=expected_statuses,
            authoritative_predicate=authoritative_predicate,
            **kwargs,
        )

    @staticmethod
    def _raw(task: Any) -> dict[str, Any]:
        return VectorIndexTaskLifecycleSupport._raw(task)

    @staticmethod
    def _envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
        return VectorIndexTaskLifecycleSupport._envelope(raw)

    def _authoritative_task_matches(
        self,
        task: Any,
        *,
        job_id: str,
        request_fingerprint: str,
        attempt_id: str,
        worker_audience: str | None = None,
    ) -> bool:
        return self._support._authoritative_task_matches(
            task,
            job_id=job_id,
            request_fingerprint=request_fingerprint,
            attempt_id=attempt_id,
            worker_audience=worker_audience,
        )

    def get_task(self, job_id: str) -> dict[str, Any] | None:
        return self._queries.get_task(job_id)

    def get_latest_completed_compatibility_state(
        self,
        *,
        trusted_scope: VectorIndexTrustedScope,
    ) -> dict[str, Any] | None:
        return self._queries.get_latest_completed_compatibility_state(trusted_scope=trusted_scope)

    @staticmethod
    def _finite_timestamp(value: Any, *, fallback: Any) -> float:
        return VectorIndexTaskLifecycleSupport._finite_timestamp(
            value,
            fallback=fallback,
        )

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
        return self._commands.submit(
            operation=operation,
            trusted_scope=trusted_scope,
            idempotency_key=idempotency_key,
            payload=payload,
            actor=actor,
            priority=priority,
        )

    def cancel(self, *, job_id: str, actor: str) -> dict[str, Any]:
        return self._commands.cancel(job_id=job_id, actor=actor)

    def retry(self, *, job_id: str, actor: str) -> dict[str, Any]:
        return self._commands.retry(job_id=job_id, actor=actor)

    def issue_dispatch_attempt(
        self,
        *,
        job_id: str,
        worker_audience: str,
        phase: str,
        actor: str = "hub-dispatch",
    ) -> dict[str, Any]:
        return self._dispatch.issue_dispatch_attempt(
            job_id=job_id,
            worker_audience=worker_audience,
            phase=phase,
            actor=actor,
        )

    def admit_dispatch_attempt(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        worker_audience: str,
        actor: str = "worker-dispatch-admission",
    ) -> dict[str, Any]:
        return self._dispatch.admit_dispatch_attempt(
            job_id=job_id,
            attempt_id=attempt_id,
            sequence=sequence,
            phase=phase,
            worker_audience=worker_audience,
            actor=actor,
        )

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._dispatch.validate_worker_result(
            job_id=job_id,
            result=result,
        )

    def _new_dispatch(
        self,
        *,
        previous: Any,
        audience: str,
        phase: str,
        issued_at: float,
    ) -> dict[str, Any]:
        return self._support._new_dispatch(
            previous=previous,
            audience=audience,
            phase=phase,
            issued_at=issued_at,
        )

    @staticmethod
    def _normalize_dispatch_audience(value: Any) -> str:
        return normalize_vector_index_dispatch_audience(value)

    @staticmethod
    def _validate_input_ref_binding(
        payload: VectorIndexOperationPayload,
        *,
        scope: VectorIndexTrustedScope,
    ) -> None:
        VectorIndexTaskCommandService._validate_input_ref_binding(
            payload,
            scope=scope,
        )

    @staticmethod
    def _validate_raw_input_ref_binding(
        input_ref: Any,
        *,
        scope: VectorIndexTrustedScope,
    ) -> None:
        VectorIndexTaskCommandService._validate_raw_input_ref_binding(
            input_ref,
            scope=scope,
        )

    def _authorize_preparation(
        self,
        payload: VectorIndexOperationPayload,
        *,
        operation: str,
        scope: VectorIndexTrustedScope,
    ) -> VectorIndexOperationPayload:
        return self._commands._authorize_preparation(
            payload,
            operation=operation,
            scope=scope,
        )

    @staticmethod
    def _validate_checkpoint_binding(
        payload: VectorIndexOperationPayload,
        *,
        scope: VectorIndexTrustedScope,
        idempotency_key: str,
    ) -> None:
        VectorIndexTaskCommandService._validate_checkpoint_binding(
            payload,
            scope=scope,
            idempotency_key=idempotency_key,
        )

    def _resume_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        raw: Mapping[str, Any],
        scope: VectorIndexTrustedScope,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._commands._resume_envelope(
            envelope,
            raw=raw,
            scope=scope,
        )

    def accept_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        status_values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._dispatch.accept_worker_result(
            job_id=job_id,
            result=result,
            status_values=status_values,
        )

    def _assert_scope_available(
        self,
        scope: VectorIndexTrustedScope,
        *,
        exclude_job_id: str | None = None,
    ) -> None:
        self._support._assert_scope_available(
            scope,
            exclude_job_id=exclude_job_id,
        )

    def _audit_task(
        self,
        action: str,
        envelope: Mapping[str, Any],
        actor: str,
    ) -> None:
        self._support._audit_task(action, envelope, actor)


__all__ = [
    "VectorIndexScopeFencePort",
    "VectorIndexTaskQueuePort",
    "VectorIndexTaskRepositoryPort",
    "VectorIndexTaskService",
]
