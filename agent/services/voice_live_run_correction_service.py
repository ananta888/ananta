from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_live_runs import (
    VoiceLiveCorrectionClaim,
    VoiceLiveRunRepository,
    VoiceLiveRunRepositoryConflict,
)
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_result_artifact_service import (
    VoiceResultArtifactService,
    get_voice_result_artifact_service,
)
from agent.services.voice_transcription_postprocessing_service import (
    VoiceTranscriptionPostprocessingService,
    get_voice_transcription_postprocessing_service,
)


@dataclass(frozen=True)
class VoiceLiveCorrectionPreparation:
    requested: bool
    configuration_digest: str | None = None
    spec_ref: str | None = None
    created_here: bool = False


class VoiceLiveCorrectionExecutionError(RuntimeError):
    """Terminal correction failure with a public, content-free reason code."""

    _REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")

    def __init__(self, reason_code: str) -> None:
        normalized = str(reason_code or "").strip().lower()
        if not self._REASON_CODE.fullmatch(normalized):
            normalized = "correction_execution_failed"
        self.reason_code = normalized
        super().__init__(normalized)


class VoiceLiveRunCorrectionService:
    """Hub-owned scheduler/executor for revisioned live-segment correction."""

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository | None = None,
        artifacts: VoiceResultArtifactService | None = None,
        tasks: VoiceLiveRunTaskPort | None = None,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        postprocessing: VoiceTranscriptionPostprocessingService | None = None,
        executor: ThreadPoolExecutor | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or VoiceLiveRunRepository()
        self._artifacts = artifacts or get_voice_result_artifact_service()
        self._tasks = tasks or VoiceLiveRunTaskPort()
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._postprocessing = postprocessing or get_voice_transcription_postprocessing_service()
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="voice-live-correction",
        )
        self._clock = clock
        self._futures: set[Future[None]] = set()
        self._scheduled_keys: set[tuple[str, str, str, int]] = set()
        self._capacity = threading.BoundedSemaphore(2)
        self._futures_lock = threading.Lock()
        self._idle_condition = threading.Condition(self._futures_lock)

    def prepare(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        *,
        sequence: int,
        provisional_result_ref: str,
        effective_configuration: Mapping[str, Any],
    ) -> VoiceLiveCorrectionPreparation:
        configuration = self._correction_configuration(effective_configuration)
        if not self.correction_requested(configuration):
            return VoiceLiveCorrectionPreparation(requested=False)
        canonical = json.dumps(
            configuration,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        request_ref = (
            f"voice-live-correction-spec:{run.id}:{sequence}:"
            f"{provisional_result_ref}:{digest}"
        )
        self._assert_run_identity(principal, run)
        artifact = self._artifacts.find_live_envelope(
            principal,
            request_ref=request_ref,
            profile_id=run.profile_id,
        )
        created_here = False
        if artifact is None:
            try:
                artifact = self._artifacts.create(
                    principal,
                    request_hash=request_ref,
                    profile_id=run.profile_id,
                    result={
                        "schema_version": "ananta.voice-live-correction-spec.v1",
                        "effective_configuration": configuration,
                        "configuration_digest": digest,
                        "candidates": [],
                    },
                )
                created_here = True
            except IntegrityError:
                artifact = self._artifacts.find_live_envelope(
                    principal,
                    request_ref=request_ref,
                    profile_id=run.profile_id,
                )
                if artifact is None:
                    raise
        try:
            self._assert_run_identity(principal, run)
        except Exception:
            if created_here:
                self._artifacts.delete(principal, str(artifact["id"]))
            raise
        return VoiceLiveCorrectionPreparation(
            requested=True,
            configuration_digest=digest,
            spec_ref=str(artifact["id"]),
            created_here=created_here,
        )

    def discard_preparation_if_unowned(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        preparation: VoiceLiveCorrectionPreparation,
    ) -> bool:
        if not preparation.created_here or not preparation.spec_ref:
            return False
        current = self._repository.get_segment(principal, run_id, sequence)
        if current is not None and current.correction_spec_ref == preparation.spec_ref:
            return False
        return self._artifacts.delete(principal, preparation.spec_ref) > 0

    def schedule(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
    ) -> bool:
        key = (principal.tenant_id, principal.subject, run_id, sequence)
        with self._futures_lock:
            if key in self._scheduled_keys or not self._capacity.acquire(blocking=False):
                return False
            self._scheduled_keys.add(key)
        try:
            future = self._executor.submit(
                self._claim_and_execute,
                principal,
                run_id,
                sequence,
            )
        except Exception:
            with self._futures_lock:
                self._scheduled_keys.discard(key)
                self._capacity.release()
            return False
        with self._futures_lock:
            self._futures.add(future)
        future.add_done_callback(lambda value: self._forget_future(value, key))
        return True

    def schedule_pending(self, principal: VoicePrincipal, run_id: str) -> int:
        return sum(
            self.schedule(principal, run_id, segment.sequence)
            for segment in self._repository.list_segments(principal, run_id)
            if segment.correction_status in {"queued", "processing"}
        )

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._idle_condition:
            while self._futures:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle_condition.wait(timeout=remaining)
            return True

    @staticmethod
    def correction_requested(configuration: Mapping[str, Any]) -> bool:
        flags = configuration.get("feature_flags")
        policy = str(configuration.get("correction_policy") or "")
        return bool(
            isinstance(flags, Mapping)
            and (
                (policy == "restricted_choice" and flags.get("restricted_worker") is True)
                or (policy == "generative_local" and flags.get("generative_judge") is True)
                or (
                    policy == "generative_rewrite"
                    and flags.get("generative_corrector") is True
                )
            )
        )

    def _execute(
        self,
        principal: VoicePrincipal,
        claim: VoiceLiveCorrectionClaim,
        configuration: dict[str, Any],
    ) -> None:
        run = claim.run
        segment = claim.segment
        provisional_ref = str(segment.provisional_result_ref or "")
        attempt_count = segment.correction_attempt_count
        task = None
        corrected_ref: str | None = None
        try:
            self._assert_correction_owned(principal, claim, expected_task_id=None)
            task = self._tasks.create_correction_child(
                principal,
                run,
                segment,
                effective_configuration=configuration,
                idempotency_key=(
                    f"voice-live-correction:{run.id}:{segment.sequence}:"
                    f"{provisional_ref}:{segment.correction_configuration_digest}:"
                    f"{attempt_count}"
                ),
            )
            self._repository.bind_correction_task(
                principal,
                run.id,
                segment.sequence,
                provisional_result_ref=provisional_ref,
                attempt_count=attempt_count,
                task_id=task.task_id,
                now=self._clock(),
            )
            self._assert_correction_owned(
                principal,
                claim,
                expected_task_id=task.task_id,
            )
            provisional = self._artifacts.get(principal, provisional_ref)
            raw_result = provisional.get("result")
            result = dict(raw_result) if isinstance(raw_result, Mapping) else {}
            outcome = self._postprocessing.apply(
                result,
                configuration,
                task,
                principal=principal,
                request_id=(
                    f"voice-live-correction-{run.id}-{segment.sequence}-{attempt_count}"
                ),
                language=run.language,
                run_id=run.id,
            )
            applied = bool(outcome.choice_applied or outcome.corrector_applied)
            reason_code = (
                outcome.corrector_reason
                if str(configuration.get("correction_policy") or "") == "generative_rewrite"
                else outcome.choice_reason
            )
            if not applied and self._failure_reason(reason_code):
                raise VoiceLiveCorrectionExecutionError(reason_code)
            result_ref = provisional_ref
            if applied:
                artifact = self._get_or_create_corrected_artifact(
                    principal,
                    run,
                    segment,
                    result=outcome.result,
                )
                corrected_ref = str(artifact["id"])
                result_ref = corrected_ref
            self._assert_correction_owned(
                principal,
                claim,
                expected_task_id=task.task_id,
            )
            self._tasks.complete_child(task, result_ref=result_ref)
            self._assert_correction_owned(
                principal,
                claim,
                expected_task_id=task.task_id,
            )
            self._repository.complete_correction(
                principal,
                run.id,
                segment.sequence,
                provisional_result_ref=provisional_ref,
                attempt_count=attempt_count,
                task_id=task.task_id,
                result_ref=result_ref,
                applied=applied,
                reason_code=reason_code,
                now=self._clock(),
            )
        except Exception as exc:
            current = self._repository.get_segment(principal, run.id, segment.sequence)
            try:
                self._assert_correction_owned(
                    principal,
                    claim,
                    expected_task_id=task.task_id if task is not None else None,
                )
                still_owned = True
            except (LookupError, VoiceLiveRunRepositoryConflict):
                still_owned = False
            if corrected_ref and (current is None or current.result_ref != corrected_ref):
                self._artifacts.delete(principal, corrected_ref)
            if task is not None:
                if still_owned:
                    self._tasks.fail_child(task, exc)
                else:
                    self._tasks.delete_child_tree(
                        principal,
                        profile_id=run.profile_id,
                        root_task_id=task.task_id,
                        expected_result_ref=corrected_ref,
                    )
            if still_owned:
                self._repository.fail_correction(
                    principal,
                    run.id,
                    segment.sequence,
                    provisional_result_ref=provisional_ref,
                    attempt_count=attempt_count,
                    failure_code=self._execution_failure_code(exc),
                    task_id=task.task_id if task is not None else None,
                    now=self._clock(),
                )

    def _claim_and_execute(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
    ) -> None:
        segment = self._repository.get_segment(principal, run_id, sequence)
        if (
            segment is None
            or not segment.provisional_result_ref
            or not segment.correction_configuration_digest
        ):
            return
        configuration: dict[str, Any] | None = None
        configuration_error: BaseException | None = None
        try:
            configuration = self._load_configuration(principal, segment)
            if self._configuration_digest(configuration) != segment.correction_configuration_digest:
                raise VoiceLiveCorrectionExecutionError("correction_configuration_digest_mismatch")
        except Exception as exc:
            configuration_error = exc
        try:
            claim = self._repository.claim_correction(
                principal,
                run_id,
                sequence,
                provisional_result_ref=segment.provisional_result_ref,
                configuration_digest=segment.correction_configuration_digest,
                now=self._clock(),
            )
        except (LookupError, VoiceLiveRunRepositoryConflict):
            return
        if not claim.claimed:
            return
        if configuration_error is not None or configuration is None:
            self._repository.fail_correction(
                principal,
                run_id,
                sequence,
                provisional_result_ref=segment.provisional_result_ref,
                attempt_count=claim.segment.correction_attempt_count,
                failure_code=(
                    "correction_spec_invalid"
                    if configuration_error is None
                    else self._spec_failure_code(configuration_error)
                ),
                task_id=None,
                now=self._clock(),
            )
            return
        self._execute(principal, claim, configuration)

    def _assert_run_identity(
        self,
        principal: VoicePrincipal,
        expected: VoiceLiveRunDB,
    ) -> None:
        deleted_at = self._tombstones.deleted_at(principal, expected.profile_id)
        current = self._repository.get(principal, expected.id)
        if (
            current is None
            or (deleted_at is not None and deleted_at >= expected.created_at)
            or current.profile_id != expected.profile_id
            or current.created_at != expected.created_at
            or current.status != "active"
        ):
            raise VoiceLiveRunRepositoryConflict("voice live correction run identity changed")

    def _assert_correction_owned(
        self,
        principal: VoicePrincipal,
        claim: VoiceLiveCorrectionClaim,
        *,
        expected_task_id: str | None,
    ) -> None:
        self._assert_run_identity(principal, claim.run)
        current = self._repository.get_segment(
            principal,
            claim.run.id,
            claim.segment.sequence,
        )
        if (
            current is None
            or current.status != "completed"
            or current.correction_status != "processing"
            or current.provisional_result_ref != claim.segment.provisional_result_ref
            or current.correction_attempt_count != claim.segment.correction_attempt_count
            or current.correction_task_id != expected_task_id
        ):
            raise VoiceLiveRunRepositoryConflict("voice live correction ownership changed")

    def _load_configuration(
        self,
        principal: VoicePrincipal,
        segment: VoiceLiveRunSegmentDB,
    ) -> dict[str, Any]:
        artifact = self._artifacts.get(principal, str(segment.correction_spec_ref or ""))
        result = artifact.get("result")
        value = result.get("effective_configuration") if isinstance(result, Mapping) else None
        if not isinstance(value, Mapping):
            raise VoiceLiveRunRepositoryConflict("voice live correction spec is invalid")
        return dict(value)

    @staticmethod
    def _configuration_digest(configuration: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            dict(configuration),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _correction_configuration(
        configuration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist only the allowlisted, non-secret correction policy."""

        allowed = {
            "correction_policy",
            "review_policy",
            "generative_corrector_provider",
            "generative_corrector_model",
            "generative_corrector_max_edit_ratio",
        }
        projected = {
            key: value for key, value in dict(configuration).items() if key in allowed
        }
        raw_flags = configuration.get("feature_flags")
        if isinstance(raw_flags, Mapping):
            projected["feature_flags"] = {
                key: bool(raw_flags[key])
                for key in (
                    "restricted_worker",
                    "generative_judge",
                    "generative_corrector",
                )
                if key in raw_flags
            }
        return projected

    @staticmethod
    def _failure_reason(reason_code: str) -> bool:
        normalized = str(reason_code or "").casefold()
        return any(
            token in normalized
            for token in (
                "failed",
                "unavailable",
                "expired",
                "invalid",
                "tracking",
                "missing",
                "blocked",
            )
        )

    @staticmethod
    def _execution_failure_code(exc: BaseException) -> str:
        if isinstance(exc, VoiceLiveCorrectionExecutionError):
            return exc.reason_code
        if isinstance(exc, TimeoutError):
            return "correction_execution_timeout"
        return "correction_execution_failed"

    @staticmethod
    def _spec_failure_code(exc: BaseException) -> str:
        if isinstance(exc, VoiceLiveCorrectionExecutionError):
            return exc.reason_code
        if isinstance(exc, TimeoutError):
            return "correction_spec_timeout"
        return "correction_spec_invalid"

    def _get_or_create_corrected_artifact(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        segment: VoiceLiveRunSegmentDB,
        *,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_ref = (
            f"voice-live-correction-result:{run.id}:{segment.sequence}:"
            f"{segment.provisional_result_ref}:{segment.correction_configuration_digest}:"
            f"{segment.correction_attempt_count}"
        )
        existing = self._artifacts.find_live_envelope(
            principal,
            request_ref=request_ref,
            profile_id=run.profile_id,
        )
        if existing is not None:
            return existing
        try:
            return self._artifacts.create(
                principal,
                request_hash=request_ref,
                profile_id=run.profile_id,
                result=dict(result),
            )
        except IntegrityError:
            recovered = self._artifacts.find_live_envelope(
                principal,
                request_ref=request_ref,
                profile_id=run.profile_id,
            )
            if recovered is None:
                raise
            return recovered

    def _forget_future(
        self,
        future: Future[None],
        key: tuple[str, str, str, int],
    ) -> None:
        with self._idle_condition:
            self._futures.discard(future)
            self._scheduled_keys.discard(key)
            self._capacity.release()
            self._idle_condition.notify_all()


voice_live_run_correction_service = VoiceLiveRunCorrectionService()


def get_voice_live_run_correction_service() -> VoiceLiveRunCorrectionService:
    return voice_live_run_correction_service
