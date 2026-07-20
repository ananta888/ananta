"""Single-job, fenced speech adaptation runner."""

from __future__ import annotations

import errno
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from worker.speech_training.backend import (
    AbortSignal,
    SpeechDatasetView,
    SpeechTrainingAborted,
    SpeechTrainingBackendError,
    SpeechTrainingContext,
)
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.checkpointing import SpeechCheckpointStore
from worker.speech_training.contracts import (
    CONTRACT_VERSION,
    RESULT_TYPE,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
    canonical_sha256,
)
from worker.speech_training.evaluation import SpeechEvaluationError, validate_evaluation_report
from worker.speech_training.result_publisher import SpeechResultPublisher

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "phase": frozenset({"phase", "step"}),
    "progress": frozenset({"step", "max_steps", "loss_micros"}),
    "checkpoint": frozenset({"step", "sha256"}),
    "evaluation": frozenset({"passed", "report_digest"}),
    "artifact": frozenset({"sha256", "size_bytes"}),
    "cleanup": frozenset({"succeeded"}),
    "status": frozenset({"status", "reason_code"}),
}


class SpeechBindingAuthority(Protocol):
    def verify(self, job: SpeechAdaptationJob, *, phase: str) -> tuple[bool, str | None]: ...


class SpeechDatasetResolver(Protocol):
    def open_admitted(self, job: SpeechAdaptationJob) -> SpeechDatasetView: ...


@dataclass(frozen=True)
class ResourceUsage:
    ram_bytes: int
    vram_bytes: int
    disk_bytes: int


class SpeechResourceProbe(Protocol):
    def sample(self, roots: tuple[Path, ...]) -> ResourceUsage: ...


class ProcessSpeechResourceProbe:
    def __init__(self, *, vram_probe: Callable[[], int] | None = None) -> None:
        self._vram_probe = vram_probe or (lambda: 0)

    def sample(self, roots: tuple[Path, ...]) -> ResourceUsage:
        try:
            import psutil

            ram = int(psutil.Process(os.getpid()).memory_info().rss)
        except (ImportError, OSError):
            ram = 0
        disk = 0
        for root in roots:
            if not root.exists():
                continue
            disk += sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        return ResourceUsage(ram_bytes=ram, vram_bytes=max(0, int(self._vram_probe())), disk_bytes=disk)


class ContentFreeEventJournal:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._events: list[dict[str, Any]] = []
        self._closed = False

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self._closed:
            raise SpeechTrainingBackendError("speech_late_event_blocked", "event journal is closed")
        allowed = _EVENT_FIELDS.get(event_type)
        if allowed is None or set(payload) - allowed:
            raise SpeechTrainingBackendError(
                "speech_event_content_forbidden",
                "speech worker event contains a forbidden type or field",
            )
        if len(self._events) >= self._maximum:
            raise SpeechTrainingBackendError("speech_event_budget_exceeded", "speech worker event budget exceeded")
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                if len(value) > 128 or "/" in value or "\\" in value:
                    raise SpeechTrainingBackendError(
                        "speech_event_content_forbidden",
                        "speech worker event contains content or a local path",
                    )
                safe[key] = value
            elif isinstance(value, (bool, int)) and not isinstance(value, float):
                safe[key] = value
            else:
                raise SpeechTrainingBackendError(
                    "speech_event_content_forbidden",
                    "speech worker event values must be bounded codes, booleans or integers",
                )
        self._events.append({"sequence": len(self._events) + 1, "type": event_type, "payload": safe})

    @property
    def digest(self) -> str:
        return canonical_sha256(self._events)

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._events)

    def close(self) -> None:
        self._closed = True


class SpeechTrainingRunner:
    def __init__(
        self,
        *,
        registry: SpeechTrainingBackendRegistry,
        authority: SpeechBindingAuthority,
        dataset_resolver: SpeechDatasetResolver,
        result_publisher: SpeechResultPublisher,
        workspace_root: Path,
        model_root: Path,
        resource_probe: SpeechResourceProbe | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._registry = registry
        self._authority = authority
        self._datasets = dataset_resolver
        self._publisher = result_publisher
        self._workspace_root = workspace_root.resolve()
        self._model_root = model_root.resolve()
        self._resources = resource_probe or ProcessSpeechResourceProbe()
        self._monotonic = monotonic
        self._clock_ms = clock_ms

    def run(self, job: SpeechAdaptationJob, *, abort: AbortSignal | None = None) -> SpeechAdaptationResult:
        signal = abort or AbortSignal()
        journal = ContentFreeEventJournal(job.budget.max_events)
        workspace = self._workspace_root / job.job_id / job.attempt.attempt_id
        checkpoint_root = workspace / "checkpoints"
        artifact_root = workspace / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        checkpoint_store = SpeechCheckpointStore(checkpoint_root)
        started = self._monotonic()
        evaluation_digest: str | None = None
        checkpoint_digest: str | None = None
        published = None
        succeeded = False
        backend = None
        context = None
        status = "failed"
        reason_code: str | None = "speech_training_failed"
        try:
            self._verify(job, "before_audio_access")
            self._enforce_resources(job, started, (workspace,))
            checkpoint_store.resolve_resume(job)
            dataset = self._datasets.open_admitted(job)
            self._verify(job, "after_dataset_open")
            context = SpeechTrainingContext(
                job=job,
                dataset=dataset,
                model_root=self._model_root,
                workspace_root=workspace,
                checkpoint_root=checkpoint_root,
                artifact_root=artifact_root,
                abort=signal,
                emit=journal.emit,
                clock_ms=self._clock_ms,
            )
            backend = self._registry.require(job.configuration.backend)
            backend.validate(context)
            prepared = backend.prepare(context)
            self._enforce_resources(job, started, (workspace,))
            state = backend.train(context, prepared)
            if state.completed_steps > job.configuration.max_steps:
                raise SpeechTrainingBackendError("speech_step_budget_exceeded", "backend exceeded admitted steps")
            self._verify(job, "before_checkpoint")
            checkpoint = backend.checkpoint(context, state)
            checkpoint_store.validate_and_bind(job, checkpoint)
            self._verify(job, "before_checkpoint_publish")
            checkpoint_receipt = self._publisher.publish_checkpoint(job, checkpoint)
            checkpoint_digest = checkpoint_receipt.sha256
            self._enforce_resources(job, started, (workspace,))
            report = backend.evaluate(context, state)
            evaluation_digest = validate_evaluation_report(report, expected_job=job)
            if not bool(report.get("passed")):
                raise SpeechTrainingBackendError(
                    "speech_evaluation_policy_failed",
                    "speech evaluation did not pass all mandatory gates",
                )
            self._verify(job, "before_evaluation_publish")
            evaluation_receipt = self._publisher.publish_evaluation(job, report)
            if evaluation_receipt.sha256 != evaluation_digest:
                raise SpeechTrainingBackendError(
                    "speech_evaluation_digest_mismatch",
                    "published evaluation report does not match the validated report",
                )
            self._verify(job, "before_artifact_export")
            artifact = backend.export(context, state, report)
            self._enforce_resources(job, started, (workspace,))
            self._verify(job, "before_artifact_publish")
            published = self._publisher.publish(job, artifact)
            self._verify(job, "after_artifact_publish")
            status = "completed"
            reason_code = None
            succeeded = True
        except SpeechTrainingBackendError as exc:
            published = None
            reason_code = exc.reason_code
            if exc.reason_code == "speech_dataset_only":
                status = "dataset_only"
                reason_code = None
            elif isinstance(exc, SpeechTrainingAborted) or exc.reason_code in {
                "speech_training_cancelled",
                "speech_deadline_expired",
                "speech_lease_expired",
                "speech_lease_lost",
                "speech_consent_revoked",
            }:
                status = "cancelled"
        except SpeechEvaluationError as exc:
            published = None
            reason_code = exc.reason_code
        except Exception:
            published = None
            reason_code = "speech_internal_failure"
        finally:
            if backend is not None and context is not None:
                try:
                    backend.cleanup(context, succeeded=succeeded)
                except SpeechTrainingBackendError:
                    if succeeded:
                        succeeded = False
                        status = "failed"
                        reason_code = "speech_cleanup_failed"
                        published = None
            try:
                shutil.rmtree(workspace)
            except FileNotFoundError:
                pass
            except OSError:
                if succeeded:
                    succeeded = False
                    status = "failed"
                    reason_code = "speech_workspace_cleanup_failed"
                    published = None
            try:
                # The attempt directory is the runner's unit of ownership;
                # remove the now-empty job parent without touching sibling
                # attempts that a restarted/fenced Hub may still reconcile.
                workspace.parent.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST} and succeeded:
                    succeeded = False
                    status = "failed"
                    reason_code = "speech_workspace_cleanup_failed"
                    published = None
            try:
                journal.emit("status", {"status": status, **({"reason_code": reason_code} if reason_code else {})})
            finally:
                journal.close()
        result_payload = {
            "contract_version": CONTRACT_VERSION,
            "result_type": RESULT_TYPE,
            "job_id": job.job_id,
            "attempt_id": job.attempt.attempt_id,
            "binding_digest": job.binding_digest,
            "fencing_digest": job.fencing.fencing_digest,
            "status": status,
            "events_digest": journal.digest,
            "evaluation_report_digest": evaluation_digest,
            "checkpoint_digest": checkpoint_digest,
            "artifact": asdict(published) if published is not None else None,
            "reason_code": reason_code,
        }
        return SpeechAdaptationResult.from_mapping(result_payload)

    def _verify(self, job: SpeechAdaptationJob, phase: str) -> None:
        active, reason = self._authority.verify(job, phase=phase)
        if not active:
            raise SpeechTrainingAborted(
                str(reason or "speech_binding_authority_denied"),
                "Hub authority rejected current consent, lease or fencing bindings",
            )

    def _enforce_resources(self, job: SpeechAdaptationJob, started: float, roots: tuple[Path, ...]) -> None:
        if self._monotonic() - started > job.budget.max_wall_seconds:
            raise SpeechTrainingAborted("speech_wall_time_exceeded", "speech training wall-time budget exceeded")
        usage = self._resources.sample(roots)
        checks = (
            (usage.ram_bytes, job.budget.max_ram_bytes, "speech_ram_budget_exceeded"),
            (usage.vram_bytes, job.budget.max_vram_bytes, "speech_vram_budget_exceeded"),
            (usage.disk_bytes, job.budget.max_disk_bytes, "speech_disk_budget_exceeded"),
        )
        for current, maximum, reason in checks:
            if current > maximum:
                raise SpeechTrainingAborted(reason, "speech training resource budget exceeded")
