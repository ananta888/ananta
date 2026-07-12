from __future__ import annotations

import hashlib
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Mapping, cast

from .backends.base import TranscriptionCandidate, TranscriptionResult, VoiceBackend
from .context import VoiceRecognitionContext
from .errors import normalize_backend_exception
from .execution_control import BackendCancellationToken
from .metrics import VoiceRuntimeMetricsPort
from .resources import (
    BackendResourceRequirement,
    ResourceAdmissionController,
    ResourceLease,
    VoiceResourceBudget,
    backend_resource_requirement,
)


@dataclass(frozen=True)
class CandidateExecutionPolicy:
    max_parallel_backends: int = 2
    deadline_seconds: float = 120.0
    audio_variant_id: str = "original"
    source_audio_id: str = ""
    backend_deadline_seconds: Mapping[str, float] = field(default_factory=dict)
    resource_budget: VoiceResourceBudget | None = None
    audio_duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.max_parallel_backends <= 0 or self.deadline_seconds <= 0:
            raise ValueError("voice candidate execution policy is invalid")
        if self.audio_duration_ms < 0:
            raise ValueError("voice candidate audio duration is invalid")
        deadlines = {
            str(backend_id): float(value)
            for backend_id, value in self.backend_deadline_seconds.items()
        }
        if any(not backend_id or value <= 0 for backend_id, value in deadlines.items()):
            raise ValueError("voice backend deadline policy is invalid")
        object.__setattr__(self, "backend_deadline_seconds", MappingProxyType(deadlines))

    def deadline_for(self, backend_id: str) -> float:
        return min(
            self.deadline_seconds,
            self.backend_deadline_seconds.get(backend_id, self.deadline_seconds),
        )


@dataclass(frozen=True)
class _ActiveExecution:
    backend_id: str
    backend: VoiceBackend
    candidate_id: str
    token: BackendCancellationToken
    lease: ResourceLease
    started_monotonic: float


@dataclass(frozen=True)
class CandidateExecutionBatch:
    candidates: tuple[TranscriptionCandidate, ...]
    results_by_candidate_id: Mapping[str, TranscriptionResult]


class ParallelCandidateExecutor:
    """Bounded local candidate executor; it never delegates Ananta tasks."""

    def __init__(
        self,
        *,
        max_inflight_candidates: int = 8,
        metrics: VoiceRuntimeMetricsPort | None = None,
        admission_controller: ResourceAdmissionController | None = None,
    ) -> None:
        maximum = max(1, int(max_inflight_candidates))
        self._admission = admission_controller or ResourceAdmissionController(
            VoiceResourceBudget(
                max_ram_bytes=2**63 - 1,
                max_vram_bytes=2**63 - 1,
                max_concurrent_backends=maximum,
                max_audio_ms=24 * 60 * 60 * 1000,
                max_queue_depth=maximum,
            )
        )
        self._metrics = metrics

    def execute(
        self,
        backends: Mapping[str, VoiceBackend],
        *,
        filename: str,
        content: bytes,
        language: str | None,
        policy: CandidateExecutionPolicy,
        context: VoiceRecognitionContext | None = None,
    ) -> tuple[TranscriptionCandidate, ...]:
        return self.execute_batch(
            backends,
            filename=filename,
            content=content,
            language=language,
            policy=policy,
            context=context,
        ).candidates

    def execute_batch(
        self,
        backends: Mapping[str, VoiceBackend],
        *,
        filename: str,
        content: bytes,
        language: str | None,
        policy: CandidateExecutionPolicy,
        context: VoiceRecognitionContext | None = None,
    ) -> CandidateExecutionBatch:
        items = sorted(backends.items(), key=lambda item: item[0])
        if not items:
            return CandidateExecutionBatch((), MappingProxyType({}))
        effective_budget = self._admission.effective_budget(policy.resource_budget)
        max_workers = max(
            1,
            min(
                int(policy.max_parallel_backends),
                effective_budget.max_concurrent_backends,
                len(items),
            ),
        )
        global_deadline = time.monotonic() + float(policy.deadline_seconds)
        source_audio_digest = policy.source_audio_id or f"audio-lineage:{uuid.uuid4().hex}"
        results: list[TranscriptionCandidate] = []
        raw_results: dict[str, TranscriptionResult] = {}
        queued = items[: effective_budget.max_queue_depth]
        for backend_id, _backend in items[effective_budget.max_queue_depth :]:
            results.append(
                self._failed_candidate(
                    backend_id,
                    content,
                    policy,
                    source_audio_digest,
                    code="resource_exhausted",
                    message="candidate queue budget exceeded",
                    retriable=True,
                )
            )
        active: dict[Future[TranscriptionResult], _ActiveExecution] = {}
        scheduling_blocked = False
        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="voice-candidate",
        )
        try:
            while queued or active:
                while queued and len(active) < max_workers and not scheduling_blocked:
                    backend_id, backend = queued.pop(0)
                    requirement = self._requirement(backend)
                    lease = self._admission.try_acquire(
                        requirement,
                        audio_ms=policy.audio_duration_ms,
                        requested_budget=policy.resource_budget,
                    )
                    if lease is None:
                        if active:
                            queued.insert(0, (backend_id, backend))
                            break
                        self._observe_admission("resource_exhausted")
                        results.append(
                            self._failed_candidate(
                                backend_id,
                                content,
                                policy,
                                source_audio_digest,
                                code="resource_exhausted",
                                message="candidate resource admission denied",
                                retriable=True,
                            )
                        )
                        continue
                    self._observe_admission("acquired")
                    started = time.monotonic()
                    deadline = min(
                        global_deadline,
                        started + policy.deadline_for(backend_id),
                    )
                    token = BackendCancellationToken(deadline_monotonic=deadline)
                    future = executor.submit(
                        self._transcribe_leased,
                        backend_id,
                        backend,
                        token=token,
                        filename=filename,
                        content=content,
                        language=language,
                        context=context,
                    )
                    active[future] = _ActiveExecution(
                        backend_id=backend_id,
                        backend=backend,
                        candidate_id=_candidate_id(
                            backend_id,
                            source_audio_digest,
                            policy.audio_variant_id,
                        ),
                        token=token,
                        lease=lease,
                        started_monotonic=started,
                    )

                if not active:
                    if scheduling_blocked:
                        self._append_unstarted_timeouts(
                            results,
                            queued,
                            content,
                            policy,
                            source_audio_digest,
                        )
                        queued.clear()
                    continue

                now = time.monotonic()
                nearest_deadline = min(
                    execution.token.deadline_monotonic for execution in active.values()
                )
                done, _pending = wait(
                    tuple(active),
                    timeout=max(0.0, nearest_deadline - now),
                    return_when=FIRST_COMPLETED,
                )
                for future in sorted(
                    done,
                    key=lambda item: active[item].backend_id,
                ):
                    execution = active.pop(future)
                    execution.lease.release()
                    candidate, raw_result = self._candidate_from_future(
                        future,
                        execution,
                        policy,
                        source_audio_digest,
                        content,
                    )
                    results.append(candidate)
                    if raw_result is not None:
                        raw_results[candidate.candidate_id] = raw_result

                now = time.monotonic()
                expired = tuple(
                    (future, execution)
                    for future, execution in active.items()
                    if now >= execution.token.deadline_monotonic
                )
                for future, execution in sorted(
                    expired,
                    key=lambda item: item[1].backend_id,
                ):
                    active.pop(future)
                    execution.token.cancel("timeout")
                    _request_backend_cancellation(execution.backend, execution.token)
                    if future.cancel():
                        execution.lease.release()
                    else:
                        future.add_done_callback(
                            partial(_release_lease, lease=execution.lease)
                        )
                        # A non-cooperative backend still occupies its worker and
                        # resources. Do not enqueue more work behind it.
                        scheduling_blocked = True
                    results.append(
                        self._failed_candidate(
                            execution.backend_id,
                            content,
                            policy,
                            source_audio_digest,
                            code="timeout",
                            message="candidate backend deadline exceeded",
                            retriable=True,
                        )
                    )
                if time.monotonic() >= global_deadline:
                    self._append_unstarted_timeouts(
                        results,
                        queued,
                        content,
                        policy,
                        source_audio_digest,
                    )
                    queued.clear()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        candidates = tuple(sorted(results, key=lambda item: item.backend))
        return CandidateExecutionBatch(
            candidates,
            MappingProxyType(dict(raw_results)),
        )

    @staticmethod
    def _requirement(backend: VoiceBackend) -> BackendResourceRequirement:
        try:
            return backend_resource_requirement(backend)
        except (TypeError, ValueError):
            # Invalid requirement contracts fail closed before model startup.
            return BackendResourceRequirement(ram_bytes=2**63)

    def _transcribe_leased(
        self,
        backend_id: str,
        backend: VoiceBackend,
        *,
        token: BackendCancellationToken,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
    ) -> TranscriptionResult:
        started = time.monotonic()
        try:
            token.raise_if_cancelled()
            result = _transcribe_backend(
                backend,
                filename=filename,
                content=content,
                language=language,
                context=context,
                token=token,
            )
            token.raise_if_cancelled()
            if self._metrics is not None:
                self._metrics.observe_backend_call(
                    operation="candidate",
                    backend=backend_id,
                    outcome="succeeded",
                    duration_seconds=time.monotonic() - started,
                )
            return result
        except Exception as exc:
            if self._metrics is not None:
                normalized = normalize_backend_exception(exc)
                self._metrics.observe_backend_call(
                    operation="candidate",
                    backend=backend_id,
                    outcome=normalized.code,
                    duration_seconds=time.monotonic() - started,
                )
            raise

    @staticmethod
    def _failed_candidate(
        backend_id: str,
        content: bytes,
        policy: CandidateExecutionPolicy,
        source_audio_digest: str,
        *,
        code: str,
        message: str,
        retriable: bool,
    ) -> TranscriptionCandidate:
        candidate_id = _candidate_id(backend_id, source_audio_digest, policy.audio_variant_id)
        return TranscriptionCandidate.failed(
            candidate_id=candidate_id,
            backend=backend_id,
            code=code,
            message=message,
            retriable=retriable,
            audio_variant_id=policy.audio_variant_id,
            source_audio_digest=source_audio_digest,
            lineage_id=candidate_id,
        )

    def _candidate_from_future(
        self,
        future: Future[TranscriptionResult],
        execution: _ActiveExecution,
        policy: CandidateExecutionPolicy,
        source_audio_digest: str,
        content: bytes,
    ) -> tuple[TranscriptionCandidate, TranscriptionResult | None]:
        try:
            result = future.result()
            return (
                TranscriptionCandidate.from_result(
                    candidate_id=execution.candidate_id,
                    backend=execution.backend_id,
                    result=result,
                    audio_variant_id=policy.audio_variant_id,
                    latency_ms=(time.monotonic() - execution.started_monotonic) * 1000.0,
                    source_audio_digest=source_audio_digest,
                    lineage_id=execution.candidate_id,
                ),
                result,
            )
        except Exception as exc:
            error = normalize_backend_exception(exc)
            if error.code in {"timeout", "cancelled"}:
                execution.token.cancel(error.code)
                _request_backend_cancellation(execution.backend, execution.token)
            return (
                self._failed_candidate(
                    execution.backend_id,
                    content,
                    policy,
                    source_audio_digest,
                    code=error.code,
                    message=error.message,
                    retriable=error.retriable,
                ),
                None,
            )

    def _append_unstarted_timeouts(
        self,
        results: list[TranscriptionCandidate],
        queued: list[tuple[str, VoiceBackend]],
        content: bytes,
        policy: CandidateExecutionPolicy,
        source_audio_digest: str,
    ) -> None:
        for backend_id, _backend in queued:
            results.append(
                self._failed_candidate(
                    backend_id,
                    content,
                    policy,
                    source_audio_digest,
                    code="timeout",
                    message="candidate deadline elapsed before backend start",
                    retriable=True,
                )
            )

    def _observe_admission(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.observe_queue_wait(
                surface="candidate_dispatch",
                outcome=outcome,
                duration_seconds=0.0,
            )


def _candidate_id(backend_id: str, source_audio_id: str, variant_id: str) -> str:
    digest = hashlib.sha256(
        backend_id.encode("utf-8")
        + b"\0"
        + variant_id.encode("utf-8")
        + b"\0"
        + source_audio_id.encode("utf-8")
    ).hexdigest()
    return f"candidate-{backend_id}-{digest[:16]}"


def _transcribe_backend(
    backend: VoiceBackend,
    *,
    filename: str,
    content: bytes,
    language: str | None,
    context: VoiceRecognitionContext | None,
    token: BackendCancellationToken,
) -> TranscriptionResult:
    capabilities: frozenset[str] = frozenset()
    projected_context: dict[str, object] = {}
    if context is not None:
        capability_method = getattr(backend, "context_capabilities", None)
        capabilities = (
            frozenset(capability_method()) if callable(capability_method) else frozenset()
        )
        projected_context = context.project(capabilities)
    controlled = getattr(backend, "transcribe_with_control", None)
    if callable(controlled):
        return cast(
            TranscriptionResult,
            controlled(
                filename=filename,
                content=content,
                language=language,
                context=projected_context,
                cancellation_token=token,
                deadline_monotonic=token.deadline_monotonic,
            ),
        )
    if context is not None:
        contextual = getattr(backend, "transcribe_with_context", None)
        if callable(contextual):
            return cast(
                TranscriptionResult,
                contextual(
                    filename=filename,
                    content=content,
                    language=language,
                    context=projected_context,
                ),
            )
    return backend.transcribe(filename=filename, content=content, language=language)


def _request_backend_cancellation(
    backend: VoiceBackend,
    token: BackendCancellationToken,
) -> None:
    callback = getattr(backend, "cancel_transcription", None)
    if not callable(callback):
        return
    try:
        callback(cancellation_token=token)
    except Exception:
        return


def _release_lease(
    _future: Future[TranscriptionResult],
    *,
    lease: ResourceLease,
) -> None:
    lease.release()
