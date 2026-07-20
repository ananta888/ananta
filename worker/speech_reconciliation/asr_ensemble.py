"""Bounded local slow-ASR ensemble for one Hub-delegated attempt."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Mapping

from voice_runtime.backends.base import (
    CandidateError,
    TranscriptionCandidate,
    TranscriptionResult,
    VoiceBackend,
)
from voice_runtime.errors import VoiceRuntimeError
from voice_runtime.execution_control import BackendCancellationToken
from voice_runtime.parallel import CandidateExecutionPolicy, ParallelCandidateExecutor
from voice_runtime.preprocessing.audio_decode import DecodedPcmAudio
from voice_runtime.preprocessing.audio_enhancement import (
    AudioEnhancementProcessor,
    DcOffsetRemovalProcessor,
    DeterministicAudioEnhancementPipeline,
    HighPassProcessor,
    LimiterProcessor,
    PeakNormalizationProcessor,
)
from voice_runtime.resources import BackendResourceRequirement
from worker.speech_reconciliation.audio_staging import SpeechAudioStagingError, StagedSpeechAudio
from worker.speech_reconciliation.contracts import (
    SpeechReconciliationPass,
    SpeechReconciliationWorkerTask,
    canonical_sha256,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+/-]{0,511}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TRANSCRIPT_CHARS = 4_000_000
_MAX_SEGMENTS = 50_000
_MAX_WORDS = 250_000


class SpeechAsrEnsembleError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True)
class LocalSpeechModel:
    model_id: str
    model_revision: str
    manifest_digest: str
    backend: VoiceBackend
    device: str = "cpu"
    ram_bytes: int = 0
    vram_bytes: int = 0
    concurrency_slots: int = 1

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.model_id) is None or _SAFE_REVISION.fullmatch(self.model_revision) is None:
            raise ValueError("speech reconciliation model identity is invalid")
        if self.model_revision.casefold() in {"latest", "main", "master"}:
            raise ValueError("speech reconciliation model revision must be immutable")
        if _DIGEST.fullmatch(self.manifest_digest) is None:
            raise ValueError("speech reconciliation model manifest digest is invalid")
        if self.device not in {"cpu", "gpu"}:
            raise ValueError("speech reconciliation model device is invalid")
        if self.ram_bytes < 0 or self.vram_bytes < 0 or self.concurrency_slots < 1:
            raise ValueError("speech reconciliation model resources are invalid")


class LocalSpeechModelRegistry:
    """Immutable allowlist; request payloads cannot introduce a model backend."""

    def __init__(self, models: Mapping[str, LocalSpeechModel]) -> None:
        normalized = dict(models)
        if not normalized or len(normalized) > 32:
            raise ValueError("speech reconciliation model allowlist is empty or too large")
        if any(key != model.model_id for key, model in normalized.items()):
            raise ValueError("speech reconciliation model allowlist key mismatch")
        self._models = normalized

    def require(self, requested: SpeechReconciliationPass) -> LocalSpeechModel:
        model = self._models.get(requested.model_id)
        if model is None:
            raise SpeechAsrEnsembleError("speech_reconciliation_model_not_allowed")
        if model.model_revision != requested.model_revision:
            raise SpeechAsrEnsembleError("speech_reconciliation_model_revision_mismatch")
        return model

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))


class SpeechAudioVariantCatalog:
    """Bounded variants backed by the canonical Voice PCM enhancement pipeline."""

    def __init__(
        self,
        variants: Mapping[str, tuple[AudioEnhancementProcessor, ...]] | None = None,
    ) -> None:
        configured = dict(variants or _default_variants())
        if "original" not in configured or not 1 <= len(configured) <= 8:
            raise ValueError("speech reconciliation audio variant allowlist is invalid")
        if any(_SAFE_ID.fullmatch(name) is None or len(processors) > 8 for name, processors in configured.items()):
            raise ValueError("speech reconciliation audio variant is invalid")
        self._variants = configured

    def render(self, audio: DecodedPcmAudio, variant_id: str, *, lineage_nonce: str) -> DecodedPcmAudio:
        processors = self._variants.get(variant_id)
        if processors is None:
            raise SpeechAsrEnsembleError("speech_reconciliation_variant_not_allowed")
        if variant_id == "original":
            return audio
        pipeline = DeterministicAudioEnhancementPipeline(lineage_nonce=lineage_nonce)
        source = pipeline.original_variant(audio)
        return pipeline.run(source, processors, label=variant_id).audio

    @property
    def variant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._variants))


@dataclass(frozen=True)
class SpeechAsrEnsembleResult:
    status: str
    candidates: tuple[TranscriptionCandidate, ...]
    completed_pass_ids: tuple[str, ...]
    failed_pass_ids: tuple[str, ...]
    candidate_set_digest: str
    reason_code: str | None = None


class _PassBudgetLease:
    def __init__(self, guard: "SpeechAsrStageGuard", *, device: str, reserved_ms: int) -> None:
        self._guard = guard
        self._device = device
        self._reserved_ms = reserved_ms
        self._closed = False

    def close(self, elapsed_ms: int) -> None:
        if self._closed:
            return
        self._closed = True
        self._guard._complete(self._device, self._reserved_ms, elapsed_ms)


class SpeechAsrStageGuard:
    """Thread-safe local projection of the admitted vector budget."""

    def __init__(
        self,
        task: SpeechReconciliationWorkerTask,
        *,
        cancellation_check: Callable[[], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._job = task.job
        self._remaining = {
            "cpu": task.budget_ledger.remaining.cpu_time_ms,
            "gpu": task.budget_ledger.remaining.gpu_time_ms,
        }
        self._wall_remaining_ms = task.budget_ledger.remaining.wall_time_ms
        self._cancel = cancellation_check
        self._monotonic = monotonic
        self._clock_ms = clock_ms
        self._started = monotonic()
        self._lock = threading.Lock()

    def before_pass(self, *, device: str, estimate_ms: int) -> _PassBudgetLease:
        self.check()
        estimate = max(1, int(estimate_ms))
        with self._lock:
            elapsed_ms = round((self._monotonic() - self._started) * 1000)
            if elapsed_ms + estimate > self._wall_remaining_ms:
                raise SpeechAsrEnsembleError("speech_reconciliation_stage_deadline_exhausted")
            if self._remaining[device] < estimate:
                raise SpeechAsrEnsembleError(f"speech_reconciliation_{device}_budget_exhausted")
            self._remaining[device] -= estimate
        return _PassBudgetLease(self, device=device, reserved_ms=estimate)

    def check(self) -> None:
        if self._cancel is not None:
            try:
                self._cancel()
            except SpeechAsrEnsembleError:
                raise
            except Exception as exc:
                raise SpeechAsrEnsembleError("speech_reconciliation_cancelled") from exc
        if self._clock_ms() >= self._job.deadline_at_ms:
            raise SpeechAsrEnsembleError("speech_reconciliation_deadline_expired")
        if round((self._monotonic() - self._started) * 1000) >= self._wall_remaining_ms:
            raise SpeechAsrEnsembleError("speech_reconciliation_stage_deadline_exhausted")

    def _complete(self, device: str, reserved_ms: int, elapsed_ms: int) -> None:
        actual = max(0, int(elapsed_ms))
        with self._lock:
            refund = max(0, reserved_ms - actual)
            self._remaining[device] += refund
            if actual > reserved_ms:
                self._remaining[device] = max(0, self._remaining[device] - (actual - reserved_ms))


class _GuardedPassBackend:
    def __init__(
        self,
        *,
        model: LocalSpeechModel,
        guard: SpeechAsrStageGuard,
        estimate_ms: int,
        monotonic: Callable[[], float],
    ) -> None:
        self._model = model
        self._guard = guard
        self._estimate_ms = estimate_ms
        self._monotonic = monotonic

    def name(self) -> str:
        return self._model.model_id

    def resource_requirements(self) -> BackendResourceRequirement:
        return BackendResourceRequirement(
            ram_bytes=self._model.ram_bytes,
            vram_bytes=self._model.vram_bytes,
            concurrency_slots=self._model.concurrency_slots,
        )

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: Mapping[str, object],
        cancellation_token: BackendCancellationToken,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        del context, deadline_monotonic
        lease = None
        started = self._monotonic()
        try:
            cancellation_token.raise_if_cancelled()
            lease = self._guard.before_pass(device=self._model.device, estimate_ms=self._estimate_ms)
            controlled = getattr(self._model.backend, "transcribe_with_control", None)
            if callable(controlled):
                return controlled(
                    filename=filename,
                    content=content,
                    language=language,
                    context={},
                    cancellation_token=cancellation_token,
                    deadline_monotonic=cancellation_token.deadline_monotonic,
                )
            return self._model.backend.transcribe(filename=filename, content=content, language=language)
        except SpeechAsrEnsembleError as exc:
            raise VoiceRuntimeError(exc.reason_code, exc.reason_code, exc.retryable) from exc
        finally:
            if lease is not None:
                lease.close(round((self._monotonic() - started) * 1000))

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        token = BackendCancellationToken(deadline_monotonic=self._monotonic() + self._estimate_ms / 1000)
        return self.transcribe_with_control(
            filename=filename,
            content=content,
            language=language,
            context={},
            cancellation_token=token,
            deadline_monotonic=token.deadline_monotonic,
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None):
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        return self._model.backend.list_models()

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()

    def cancel_transcription(self, *, cancellation_token: BackendCancellationToken) -> None:
        callback = getattr(self._model.backend, "cancel_transcription", None)
        if callable(callback):
            callback(cancellation_token=cancellation_token)


class SpeechAsrEnsemble:
    """Runs only admitted local models/variants through Voice's bounded executor."""

    def __init__(
        self,
        *,
        models: LocalSpeechModelRegistry,
        variants: SpeechAudioVariantCatalog | None = None,
        executor: ParallelCandidateExecutor | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._models = models
        self._variants = variants or SpeechAudioVariantCatalog()
        self._executor = executor or ParallelCandidateExecutor(max_inflight_candidates=8)
        self._monotonic = monotonic

    def run(
        self,
        task: SpeechReconciliationWorkerTask,
        staged: StagedSpeechAudio,
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> SpeechAsrEnsembleResult:
        # Validate the entire plan before the first model or variant is touched.
        bound_models = {item.pass_id: self._models.require(item) for item in task.execution_plan.passes}
        for item in task.execution_plan.passes:
            if item.variant_id not in self._variants.variant_ids:
                raise SpeechAsrEnsembleError("speech_reconciliation_variant_not_allowed")
        guard = SpeechAsrStageGuard(task, cancellation_check=cancellation_check, monotonic=self._monotonic)
        results: list[tuple[SpeechReconciliationPass, TranscriptionCandidate]] = []
        failure_reason: str | None = None
        grouped = _group_by_variant(task.execution_plan.passes)
        for variant_id in sorted(grouped):
            try:
                guard.check()
                audio = self._variants.render(
                    staged.decoded,
                    variant_id,
                    lineage_nonce=f"{task.binding_digest}:{variant_id}",
                )
                wav = audio.to_wav_bytes()
                passes = grouped[variant_id]
                backends = {
                    item.pass_id: _GuardedPassBackend(
                        model=bound_models[item.pass_id],
                        guard=guard,
                        estimate_ms=min(task.execution_plan.pass_deadline_ms, max(1, audio.duration_ms)),
                        monotonic=self._monotonic,
                    )
                    for item in passes
                }
                candidates = self._executor.execute(
                    backends,
                    filename=f"{variant_id}.wav",
                    content=wav,
                    language=_shared_language(passes),
                    policy=CandidateExecutionPolicy(
                        max_parallel_backends=task.execution_plan.max_parallel_passes,
                        deadline_seconds=min(
                            task.execution_plan.pass_deadline_ms,
                            task.budget_ledger.remaining.wall_time_ms,
                        )
                        / 1000,
                        audio_variant_id=variant_id,
                        source_audio_id=staged.source_audio_digest,
                        audio_duration_ms=audio.duration_ms,
                    ),
                )
                by_pass = {candidate.backend: candidate for candidate in candidates}
                for requested in passes:
                    raw = by_pass[requested.pass_id]
                    bound = _bind_candidate(
                        raw,
                        task=task,
                        requested=requested,
                        model=bound_models[requested.pass_id],
                        source_digest=staged.source_audio_digest,
                    )
                    results.append((requested, bound))
                    if (
                        failure_reason is None
                        and bound.error is not None
                        and bound.error.code.startswith("speech_reconciliation_")
                    ):
                        failure_reason = bound.error.code
            except (SpeechAsrEnsembleError, SpeechAudioStagingError) as exc:
                failure_reason = exc.reason_code
                for requested in grouped[variant_id]:
                    results.append(
                        (
                            requested,
                            _failed_candidate(
                                task,
                                requested,
                                bound_models[requested.pass_id],
                                staged.source_audio_digest,
                                exc.reason_code,
                            ),
                        )
                    )
                if exc.reason_code in {
                    "speech_reconciliation_cancelled",
                    "speech_reconciliation_deadline_expired",
                    "speech_reconciliation_stage_deadline_exhausted",
                    "speech_reconciliation_cpu_budget_exhausted",
                    "speech_reconciliation_gpu_budget_exhausted",
                }:
                    break
        completed_rows = {requested.pass_id for requested, _candidate in results}
        if len(completed_rows) != len(task.execution_plan.passes):
            reason = failure_reason or "speech_reconciliation_pass_not_started"
            for requested in task.execution_plan.passes:
                if requested.pass_id in completed_rows:
                    continue
                results.append(
                    (
                        requested,
                        _failed_candidate(
                            task,
                            requested,
                            bound_models[requested.pass_id],
                            staged.source_audio_digest,
                            reason,
                        ),
                    )
                )
        results.sort(key=lambda item: item[0].pass_id)
        candidates = _bind_variant_lineage(tuple(results))
        completed = tuple(
            requested.pass_id
            for requested, candidate in zip((item[0] for item in results), candidates, strict=True)
            if candidate.status == "succeeded"
        )
        failed = tuple(
            requested.pass_id
            for requested, candidate in zip((item[0] for item in results), candidates, strict=True)
            if candidate.status != "succeeded"
        )
        status = "completed" if completed and not failed else "partial" if completed else "failed"
        return SpeechAsrEnsembleResult(
            status=status,
            candidates=candidates,
            completed_pass_ids=completed,
            failed_pass_ids=failed,
            candidate_set_digest=canonical_sha256([_stable_candidate(item) for item in candidates]),
            reason_code=failure_reason if status != "completed" else None,
        )


def _default_variants() -> dict[str, tuple[AudioEnhancementProcessor, ...]]:
    return {
        "original": (),
        "normalized": (DcOffsetRemovalProcessor(), PeakNormalizationProcessor(), LimiterProcessor()),
        "high_pass": (HighPassProcessor(),),
        "speech_safe": (
            DcOffsetRemovalProcessor(),
            HighPassProcessor(),
            PeakNormalizationProcessor(max_gain=4.0),
            LimiterProcessor(),
        ),
    }


def _group_by_variant(
    passes: tuple[SpeechReconciliationPass, ...],
) -> dict[str, tuple[SpeechReconciliationPass, ...]]:
    result: dict[str, list[SpeechReconciliationPass]] = {}
    for item in passes:
        result.setdefault(item.variant_id, []).append(item)
    return {key: tuple(sorted(value, key=lambda item: item.pass_id)) for key, value in result.items()}


def _shared_language(passes: tuple[SpeechReconciliationPass, ...]) -> str | None:
    values = {item.language for item in passes}
    if len(values) > 1:
        raise SpeechAsrEnsembleError("speech_reconciliation_variant_language_conflict")
    return next(iter(values))


def _candidate_id(task: SpeechReconciliationWorkerTask, requested: SpeechReconciliationPass) -> str:
    digest = hashlib.sha256(
        (
            f"ananta.speech-reconciliation-candidate.v1\0{task.job.job_id}\0{task.job.attempt_id}\0"
            f"{task.job.fencing_epoch}\0{requested.pass_id}\0{requested.model_id}\0"
            f"{requested.model_revision}\0{requested.variant_id}\0{task.audio_artifact.content_digest}"
        ).encode()
    ).hexdigest()
    return f"reconciliation-candidate-{digest}"


def _bind_candidate(
    candidate: TranscriptionCandidate,
    *,
    task: SpeechReconciliationWorkerTask,
    requested: SpeechReconciliationPass,
    model: LocalSpeechModel,
    source_digest: str,
) -> TranscriptionCandidate:
    if candidate.status == "succeeded" and not _candidate_shape_is_bounded(candidate, task):
        return _failed_candidate(
            task,
            requested,
            model,
            source_digest,
            "speech_reconciliation_candidate_contract_invalid",
        )
    candidate_id = _candidate_id(task, requested)
    error = None
    if candidate.error is not None:
        code = _safe_backend_code(candidate.error.code)
        error = CandidateError(code=code, message=code, retriable=candidate.error.retriable)
    words = tuple(replace(word, candidate_id=candidate_id) for word in candidate.words)
    segments = tuple(
        replace(
            segment,
            candidate_id=candidate_id,
            words=tuple(replace(word, candidate_id=candidate_id) for word in segment.words),
        )
        for segment in candidate.segments
    )
    provenance = {
        "contract_version": "ananta.speech-reconciliation-candidate.v1",
        "job_id": task.job.job_id,
        "attempt_id": task.job.attempt_id,
        "fencing_epoch": task.job.fencing_epoch,
        "model_id": model.model_id,
        "model_revision": model.model_revision,
        "manifest_digest": model.manifest_digest,
        "variant_id": requested.variant_id,
        "source_audio_digest": source_digest,
        "duration_ms": candidate.duration_ms,
        "latency_ms": round(candidate.latency_ms or 0),
        "execution_location": "speech-reconciliation-worker",
        "synthetic": False,
    }
    return replace(
        candidate,
        candidate_id=candidate_id,
        backend=model.model_id,
        model=model.model_id,
        model_revision=model.model_revision,
        device=model.device,
        execution_location="speech-reconciliation-worker",
        manifest_digest=model.manifest_digest,
        synthetic=False,
        audio_variant_id=requested.variant_id,
        source_audio_digest=source_digest,
        lineage_id=candidate_id,
        error=error,
        words=words,
        segments=segments,
        provenance=provenance,
        parent_candidate_ids=(),
    )


def _failed_candidate(
    task: SpeechReconciliationWorkerTask,
    requested: SpeechReconciliationPass,
    model: LocalSpeechModel,
    source_digest: str,
    reason_code: str,
) -> TranscriptionCandidate:
    candidate = TranscriptionCandidate.failed(
        candidate_id=_candidate_id(task, requested),
        backend=model.model_id,
        code=_safe_backend_code(reason_code),
        message=_safe_backend_code(reason_code),
        retriable=False,
        audio_variant_id=requested.variant_id,
        source_audio_digest=source_digest,
    )
    return _bind_candidate(
        candidate,
        task=task,
        requested=requested,
        model=model,
        source_digest=source_digest,
    )


def _bind_variant_lineage(
    rows: tuple[tuple[SpeechReconciliationPass, TranscriptionCandidate], ...],
) -> tuple[TranscriptionCandidate, ...]:
    original_by_model = {
        requested.model_id: candidate for requested, candidate in rows if requested.variant_id == "original"
    }
    result: list[TranscriptionCandidate] = []
    for requested, candidate in rows:
        parent = original_by_model.get(requested.model_id)
        if requested.variant_id == "original" or parent is None:
            result.append(candidate)
            continue
        result.append(
            replace(
                candidate,
                lineage_id=parent.candidate_id,
                parent_candidate_ids=(parent.candidate_id,),
                provenance={
                    **dict(candidate.provenance),
                    "lineage_relation": "audio_variant",
                    "audio_variant_profile": requested.variant_id,
                },
            )
        )
    return tuple(result)


def _safe_backend_code(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in str(value).casefold())[:96]
    return normalized or "speech_reconciliation_asr_failed"


def _candidate_shape_is_bounded(
    candidate: TranscriptionCandidate,
    task: SpeechReconciliationWorkerTask,
) -> bool:
    if (
        not candidate.text.strip()
        or len(candidate.text) > _MAX_TRANSCRIPT_CHARS
        or len(candidate.segments) > _MAX_SEGMENTS
        or len(candidate.words) > _MAX_WORDS
        or candidate.duration_ms is None
        or not 0 <= candidate.duration_ms <= task.audio_artifact.duration_ms
        or candidate.confidence is not None
        and not 0 <= candidate.confidence <= 1
    ):
        return False
    for segment in candidate.segments:
        if not 0 <= segment.start_ms <= segment.end_ms <= task.audio_artifact.duration_ms:
            return False
        if len(segment.text) > _MAX_TRANSCRIPT_CHARS:
            return False
        for word in segment.words:
            if not 0 <= word.start_ms <= word.end_ms <= task.audio_artifact.duration_ms:
                return False
    return all(0 <= word.start_ms <= word.end_ms <= task.audio_artifact.duration_ms for word in candidate.words)


def _stable_candidate(candidate: TranscriptionCandidate) -> dict[str, object]:
    payload = candidate.as_dict()
    payload.pop("latency_ms", None)
    payload.pop("real_time_factor", None)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("latency_ms", None)
    return payload


__all__ = [
    "LocalSpeechModel",
    "LocalSpeechModelRegistry",
    "SpeechAsrEnsemble",
    "SpeechAsrEnsembleError",
    "SpeechAsrEnsembleResult",
    "SpeechAsrStageGuard",
    "SpeechAudioVariantCatalog",
]
