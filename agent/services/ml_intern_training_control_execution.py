"""Worker execution and reconciliation behavior for Hub-owned LoRA jobs."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Mapping

from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepositoryConflict,
)
from agent.services.ml_intern_training_contract import (
    MlInternTrainingContractError,
    sanitize_event_payload,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal

_CHECKPOINT_REF_PREFIX = "lora-checkpoint-v1:"


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _tenant_scope_digest(principal: MlInternTrainingPrincipal) -> str:
    """Return an opaque, versioned worker-bound scope without leaking identity."""

    material = (f"ananta.ml-intern-training.scope.v1\x00{principal.tenant_id}\x00{principal.subject}").encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _tenant_storage_key(principal: MlInternTrainingPrincipal) -> str:
    return hashlib.sha256(principal.tenant_id.encode("utf-8")).hexdigest()


class MlInternTrainingControlExecutionMixin:
    """Execute already-admitted jobs without taking ownership of orchestration."""

    def _execute_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> None:
        attempt: MlInternTrainingAttemptDB | None = None
        execution_slot_acquired = False
        execution_slot_deferred = False
        fencing_token = 0
        worker_job_id: str | None = None
        worker_ref = "local:hub-dry-run"
        task_id = job_id
        try:
            now = self._clock()
            execution_slot = self._repository.try_acquire_execution_slot(
                job_id,
                limit=self._max_concurrent_jobs(),
                now=now,
                lease_expires_at=now + int(self._config.get("timeout_seconds") or 3600),
            )
            if execution_slot is None:
                execution_slot_deferred = True
                return
            execution_slot_acquired = True
            with self._lock:
                if not self._accepting_claims:
                    return
                job = self._repository.get_job(principal, job_id)
                if job is None or job.status != "queued":
                    return
                worker_id = str(getattr(self._execution_port, "worker_id", "hub-local-dry-run"))[:191]
                worker_ref = str(getattr(self._execution_port, "worker_ref", "local:hub-dry-run"))[:512]
                task_id = job.task_id
                tenant_scope_digest = _tenant_scope_digest(principal)
                attempt_number = self._repository.next_attempt_number(job.id)
                # The high bits are the durable, strictly monotone attempt
                # number; 128 cryptographic random low bits make the bearer
                # fence non-guessable without sacrificing restart ordering.
                fencing_token = (attempt_number << 128) | secrets.randbits(128)
                attempt = self._repository.create_attempt(
                    MlInternTrainingAttemptDB(
                        job_id=job.id,
                        tenant_id=principal.tenant_id,
                        owner_subject=principal.subject,
                        attempt_number=attempt_number,
                        status="claimed",
                        worker_id=worker_id,
                        worker_url=worker_ref,
                        fencing_token_digest=hashlib.sha256(str(fencing_token).encode()).hexdigest(),
                        lease_expires_at=self._clock() + int(self._config.get("timeout_seconds") or 3600),
                        deadline_at=self._clock() + int(self._config.get("timeout_seconds") or 3600),
                    )
                )
                job.active_attempt_id = attempt.id
                job = self._transition(principal, job, "claimed", phase="claimed", progress_percent=0.5)
                worker_job_id = self._worker_job_projection.claim(
                    task_id=job.task_id,
                    job_id=job.id,
                    attempt_id=attempt.id,
                    worker_id=worker_id,
                    worker_ref=worker_ref,
                    backend=job.backend,
                    gpu_profile=str(job.request_spec.get("gpu_profile") or "none"),
                    tenant_scope_digest=tenant_scope_digest,
                )
                job.worker_job_id = worker_job_id
                job = self._repository.save_job(job, expected_version=job.version)
                job = self._transition(principal, job, "running", phase="preparing", progress_percent=1.0)
                attempt.status = "running"
                attempt.last_heartbeat_at = self._clock()
                attempt = self._repository.save_attempt(attempt, expected_version=attempt.version)
                # The durable running state now owns the slot; the transient
                # reservation only protects the submit-to-claim interval.
                self._scheduled_job_ids.discard(job_id)
                self._dispatch_queued_jobs_locked()
            dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
            if dataset is None:
                raise MlInternTrainingContractError("dataset_not_found", "dataset disappeared", status_code=404)
            dataset_path = Path(dataset.train_storage_ref or dataset.storage_ref)
            validation_path = Path(dataset.validation_storage_ref) if dataset.validation_storage_ref else None

            def on_event(event: Mapping[str, Any]) -> None:
                self._apply_execution_event(principal, job_id, attempt.id, event)

            def cancelled() -> bool:
                current = self._repository.get_job(principal, job_id)
                return bool(current and current.cancel_requested)

            execution_spec = copy.deepcopy(job.request_spec)
            execution_spec["_tenant_scope_digest"] = _tenant_scope_digest(principal)
            execution_spec["_tenant_storage_key"] = _tenant_storage_key(principal)
            resume_checkpoint = self._decode_checkpoint_ref(job.checkpoint_ref)
            if (
                resume_checkpoint is not None
                and job.job_type == "train_lora"
                and job.request_spec.get("resume_allowed", True) is True
            ):
                execution_spec["resume_checkpoint"] = resume_checkpoint
            if self._execution_port is not None and (
                job.job_type == "evaluate_lora" or job.mode == "live" or job.backend != "mock"
            ):
                result = self._execution_port.execute(
                    job_id=job.id,
                    spec=execution_spec,
                    dataset_path=dataset_path,
                    validation_path=validation_path,
                    attempt_id=attempt.id,
                    fencing_token=fencing_token,
                    on_event=on_event,
                    cancel_check=cancelled,
                )
            else:
                result = self._execute_local_bounded(job, dataset_path)
            current = self._repository.get_job(principal, job_id)
            if current is None:
                return
            if not self._attempt_owns_job(current, attempt.id) or not self._attempt_token_is_live(
                attempt.id, fencing_token
            ):
                self._record_stale_attempt_signal(principal, current, attempt.id, signal_type="result")
                return
            if current.status == "cancel_requested" or bool(result.get("cancelled")):
                raw_cancel_mode = str(result.get("cancel_mode") or "").strip().lower()
                cancel_mode = "cooperative" if raw_cancel_mode == "graceful" else raw_cancel_mode
                if cancel_mode in {"cooperative", "forced"}:
                    current.result_summary = {**dict(current.result_summary or {}), "cancel_mode": cancel_mode}
                self._transition(principal, current, "cancelled", phase="cancelled", progress_percent=100.0)
                self._finish_attempt(attempt, "cancelled")
                return
            status = str(result.get("status") or "failed")
            if status in {"completed", "trained", "dry_run_completed", "succeeded"}:
                current.result_ref = str(result.get("result_ref") or f"training-result:{job_id}")
                adapter_id = str(result.get("adapter_id") or current.request_spec.get("adapter_id") or "") or None
                if self._result_publisher is not None and result.get("artifacts"):
                    if current.job_type == "evaluate_lora":
                        adapter_id = self._result_publisher.publish_evaluation(
                            current,
                            {
                                **dict(result),
                                "_hub_execution_evidence": {
                                    "attempt_id": attempt.id,
                                    "fencing_token": fencing_token,
                                    "tenant_scope_digest": _tenant_scope_digest(principal),
                                },
                            },
                        )
                    elif current.job_type == "train_lora":
                        adapter_id = self._result_publisher.publish(current, result)
                current.adapter_id = adapter_id
                terminal_checkpoint_ref = self._checkpoint_ref_from_mapping(result.get("resume_checkpoint"))
                if terminal_checkpoint_ref is not None:
                    current.checkpoint_ref = terminal_checkpoint_ref
                current.result_summary = self._safe_result_summary(result)
                self._transition(principal, current, "completed", phase="completed", progress_percent=100.0)
                self._finish_attempt(
                    attempt,
                    "completed",
                    checkpoint_ref=terminal_checkpoint_ref,
                    result_ref=current.result_ref,
                )
            else:
                current.error_code = str(result.get("error_code") or "training_failed")[:128]
                current.error_message = str(result.get("error_message") or "training worker reported failure")[:512]
                current.retryable = bool(result.get("retryable", False))
                self._transition(principal, current, "failed", phase="failed", progress_percent=100.0)
                self._finish_attempt(attempt, "failed", error_code=current.error_code)
        except Exception as exc:
            if isinstance(exc, MlInternTrainingRepositoryConflict) and str(exc) == "attempt_number_conflict":
                # Another Hub replica won the unique (job, attempt_number)
                # compare-and-set race. Its claim is authoritative.
                return
            current = self._repository.get_job(principal, job_id)
            if current is None or current.status in {"cancelled", "completed", "failed"}:
                return
            if attempt is not None and not self._attempt_owns_job(current, attempt.id):
                return
            if attempt is not None and not self._attempt_token_is_live(attempt.id, fencing_token):
                return
            if attempt is None and current.active_attempt_id is not None:
                return
            current.error_code = str(getattr(exc, "reason_code", "training_control_failed"))[:128]
            current.error_message = str(exc)[:512]
            current.retryable = bool(getattr(exc, "retryable", False))
            try:
                self._transition(principal, current, "failed", phase="failed", progress_percent=100.0)
                self._finish_attempt(attempt, "failed", error_code=current.error_code)
            except Exception:
                return
        finally:
            self._finalize_execution_dispatch(
                principal=principal,
                job_id=job_id,
                worker_job_id=worker_job_id,
                task_id=task_id,
                worker_ref=worker_ref,
                release_execution_slot=execution_slot_acquired,
                capacity_deferred=execution_slot_deferred,
            )

    def _finalize_execution_dispatch(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        worker_job_id: str | None,
        task_id: str,
        worker_ref: str,
        release_execution_slot: bool,
        capacity_deferred: bool,
    ) -> None:
        """Release one execution lease and update non-authoritative projections."""

        if worker_job_id is not None:
            try:
                projected = self._repository.get_job(principal, job_id)
                projected_status = str(projected.status if projected is not None else "failed")
                if projected_status not in {"completed", "failed", "cancelled"}:
                    projected_status = "interrupted"
                reason_code = (
                    str(projected.error_code)
                    if projected is not None and projected.error_code
                    else ("attempt_interrupted" if projected_status == "interrupted" else None)
                )
                self._worker_job_projection.finish(
                    worker_job_id=worker_job_id,
                    task_id=task_id,
                    worker_ref=worker_ref,
                    status=projected_status,
                    reason_code=reason_code,
                )
            except Exception:
                # The domain job/attempt is authoritative. A projection
                # failure must not rewrite an already durable outcome.
                pass
        if release_execution_slot:
            try:
                self._repository.release_execution_slot(job_id)
            except Exception:
                pass
        with self._lock:
            self._scheduled_job_ids.discard(job_id)
            if capacity_deferred:
                return
            current = self._repository.get_job(principal, job_id)
            # A reconciler may have fenced and re-queued this exact job while
            # the superseded call was still unwinding.
            excluded = {job_id} if current is not None and current.status == "queued" else set()
            self._dispatch_queued_jobs_locked(excluded_job_ids=excluded)

    def _dispatch_queued_jobs_locked(self, *, excluded_job_ids: set[str] | None = None) -> None:
        """Reserve free slots and submit queued jobs in tenant round-robin order.

        The caller holds ``self._lock``. Reservations cover the short interval
        between executor submission and the durable ``claimed`` transition so
        that a slow executor cannot oversubscribe GPU capacity.
        """

        if not self._accepting_claims:
            return
        excluded = excluded_job_ids or set()
        queued = self._repository.list_queued_jobs(limit=self._max_outstanding_jobs())
        queued_ids = {job.id for job in queued}
        self._scheduled_job_ids.intersection_update(queued_ids)
        waiting = [job for job in queued if job.id not in self._scheduled_job_ids and job.id not in excluded]
        available = max(
            0,
            self._max_concurrent_jobs() - self._repository.count_executing_jobs() - len(self._scheduled_job_ids),
        )
        ordered = self._fair_queue_order(waiting)
        selected = ordered[:available]
        for job in selected:
            self._set_queue_position(job, None)
            self._scheduled_job_ids.add(job.id)
            self._last_dispatched_tenant = job.tenant_id

        remaining = [job for job in waiting if job.id not in {selected_job.id for selected_job in selected}]
        for position, job in enumerate(self._fair_queue_order(remaining), start=1):
            self._set_queue_position(job, position)

        for job in selected:
            principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
            try:
                future = self._executor.submit(self._execute_job, principal, job.id)
                if isinstance(future, Future):
                    self._futures.add(future)
                    future.add_done_callback(self._forget_future)
            except Exception:
                self._scheduled_job_ids.discard(job.id)
                current = self._repository.get_job(principal, job.id)
                if current is not None and current.status == "queued":
                    self._repository.append_event(
                        principal,
                        job.id,
                        event_type="dispatch_deferred",
                        dedupe_key=f"dispatch-deferred-{current.version}",
                        payload={
                            "status": "queued",
                            "phase": "queued",
                            "reason_code": "hub_executor_unavailable",
                            "retryable": True,
                        },
                    )
        if selected:
            queued_after_submit = self._repository.list_queued_jobs(limit=self._max_outstanding_jobs())
            waiting_after_submit = [
                job for job in queued_after_submit if job.id not in self._scheduled_job_ids and job.id not in excluded
            ]
            for position, job in enumerate(self._fair_queue_order(waiting_after_submit), start=1):
                self._set_queue_position(job, position)

    def _fair_queue_order(self, jobs: list[MlInternTrainingJobDB]) -> list[MlInternTrainingJobDB]:
        """Build a deterministic tenant round-robin projection of queued jobs."""

        by_tenant: dict[str, list[MlInternTrainingJobDB]] = {}
        tenant_order: list[str] = []
        for job in sorted(jobs, key=lambda item: (item.created_at, item.id)):
            if job.tenant_id not in by_tenant:
                by_tenant[job.tenant_id] = []
                tenant_order.append(job.tenant_id)
            by_tenant[job.tenant_id].append(job)
        if self._last_dispatched_tenant in tenant_order:
            pivot = tenant_order.index(self._last_dispatched_tenant) + 1
            tenant_order = tenant_order[pivot:] + tenant_order[:pivot]
        ordered: list[MlInternTrainingJobDB] = []
        while any(by_tenant.values()):
            for tenant_id in tenant_order:
                queue = by_tenant[tenant_id]
                if queue:
                    ordered.append(queue.pop(0))
        return ordered

    def _set_queue_position(self, job: MlInternTrainingJobDB, position: int | None) -> None:
        if job.queue_position == position:
            return
        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        current = self._repository.get_job(principal, job.id)
        if current is None or current.status != "queued" or current.queue_position == position:
            return
        expected = current.version
        current.queue_position = position
        try:
            saved = self._repository.save_job(current, expected_version=expected)
            self._repository.append_event(
                principal,
                saved.id,
                event_type="queue_position_changed",
                dedupe_key=f"queue-position-{saved.version}",
                payload={
                    "status": "queued",
                    "phase": "queued",
                    "queue_position": position,
                    "progress_percent": saved.progress_percent,
                },
            )
        except MlInternTrainingRepositoryConflict:
            return

    def _max_concurrent_jobs(self) -> int:
        return max(1, min(int(self._config.get("max_concurrent_jobs") or 1), 16))

    def _max_queued_jobs(self) -> int:
        return max(0, min(int(self._config.get("max_queued_jobs") or 0), 10_000))

    def _max_outstanding_jobs(self) -> int:
        return max(1, self._max_concurrent_jobs() + self._max_queued_jobs())

    def _finish_attempt(
        self,
        attempt: MlInternTrainingAttemptDB | None,
        status: str,
        *,
        error_code: str | None = None,
        checkpoint_ref: str | None = None,
        result_ref: str | None = None,
    ) -> None:
        if attempt is None:
            return
        current = self._repository.get_attempt(attempt.id)
        if current is None or current.status in {"interrupted", "cancelled", "completed", "failed"}:
            return
        current.status = status
        current.error_code = error_code
        if checkpoint_ref is not None:
            current.checkpoint_ref = checkpoint_ref
        if result_ref is not None:
            current.result_ref = result_ref
        current.finished_at = self._clock()
        current.last_heartbeat_at = self._clock()
        try:
            self._repository.save_attempt(current, expected_version=current.version)
        except MlInternTrainingRepositoryConflict:
            return

    def _execute_local_bounded(self, job: MlInternTrainingJobDB, dataset_path: Path) -> Mapping[str, Any]:
        if job.mode == "live" and job.backend != "mock":
            return {"status": "failed", "error_code": "training_worker_required", "retryable": True}
        from agent.services.ml_intern_training_job_service import MlInternTrainingJobService

        artifact_root = str(self._config.get("artifact_root") or "artifacts/lora")
        cfg = {
            **self._config,
            "enabled": True,
            "mode": job.mode,
            "backend": job.backend,
            "dataset_root": str(dataset_path.parent),
            "artifact_root": artifact_root,
        }
        spec = self._legacy_spec(job.request_spec, dataset_path.name)
        result = MlInternTrainingJobService(cfg).submit_job(spec)
        return {
            "status": ("completed" if result.status == "dry_run_completed" else result.status),
            "error_code": "legacy_job_failed" if result.errors else None,
            "error_message": "; ".join(result.errors)[:512],
            "result_ref": f"training-result:{job.id}",
        }

    def _apply_execution_event(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        attempt_id: str,
        event: Mapping[str, Any],
    ) -> None:
        safe = sanitize_event_payload(event)
        current = self._repository.get_job(principal, job_id)
        if current is None:
            return
        if not self._attempt_owns_job(current, attempt_id):
            self._record_stale_attempt_signal(principal, current, attempt_id, signal_type="event")
            return
        active_attempt = self._repository.get_attempt(attempt_id)
        if active_attempt is None or active_attempt.status not in {"claimed", "running"}:
            self._record_stale_attempt_signal(principal, current, attempt_id, signal_type="event")
            return
        attempt_expected = active_attempt.version
        active_attempt.last_heartbeat_at = self._clock()
        active_attempt.lease_expires_at = self._clock() + int(self._config.get("timeout_seconds") or 3600)
        self._repository.renew_execution_slot(
            job_id,
            lease_expires_at=active_attempt.lease_expires_at,
        )
        checkpoint_ref = self._checkpoint_ref_from_mapping(event.get("resume_checkpoint"))
        if checkpoint_ref is None:
            legacy_ref = safe.get("checkpoint_ref")
            checkpoint_ref = legacy_ref[:512] if isinstance(legacy_ref, str) and legacy_ref else None
        if isinstance(checkpoint_ref, str) and checkpoint_ref:
            active_attempt.checkpoint_ref = checkpoint_ref
            current.checkpoint_ref = checkpoint_ref
            safe["checkpoint_ref"] = f"checkpoint:{hashlib.sha256(checkpoint_ref.encode()).hexdigest()[:24]}"
        try:
            self._repository.save_attempt(active_attempt, expected_version=attempt_expected)
        except MlInternTrainingRepositoryConflict:
            return
        expected = current.version
        progress = safe.get("progress_percent")
        if isinstance(progress, (int, float)):
            current.progress_percent = max(current.progress_percent, min(99.0, max(0.0, float(progress))))
        for target, source in (
            ("train_loss", "train_loss"),
            ("eval_loss", "eval_loss"),
            ("learning_rate", "learning_rate"),
        ):
            if source in safe:
                setattr(current, target, safe[source])
        if "current_step" in safe:
            current.current_step = max(int(current.current_step or 0), int(safe["current_step"]))
        if "max_steps" in safe:
            current.max_steps = max(int(current.max_steps or 0), int(safe["max_steps"]))
        if "epoch" in safe:
            current.epoch = max(float(current.epoch or 0.0), float(safe["epoch"]))
        current.phase = str(safe.get("phase") or current.phase)[:64]
        try:
            saved = self._repository.save_job(current, expected_version=expected)
        except MlInternTrainingRepositoryConflict:
            return
        sequence_hint = str(event.get("event_id") or event.get("sequence") or saved.version)
        self._repository.append_event(
            principal,
            job_id,
            event_type=str(event.get("type") or "progress")[:64],
            dedupe_key=f"worker-{attempt_id}-{sequence_hint}"[:191],
            payload=safe,
        )

    def _record_stale_attempt_signal(
        self,
        principal: MlInternTrainingPrincipal,
        job: MlInternTrainingJobDB,
        attempt_id: str,
        *,
        signal_type: str,
    ) -> None:
        """Audit one content-free marker per fenced attempt/signal class."""

        try:
            self._repository.append_event(
                principal,
                job.id,
                event_type="stale_attempt_signal_ignored",
                dedupe_key=f"stale-{attempt_id}-{signal_type}"[:191],
                payload={
                    "status": job.status,
                    "phase": job.phase,
                    "reason_code": "attempt_fenced_or_superseded",
                    "signal_type": signal_type,
                },
            )
        except (KeyError, MlInternTrainingRepositoryConflict):
            return

    @staticmethod
    def _attempt_owns_job(job: MlInternTrainingJobDB, attempt_id: str) -> bool:
        return job.active_attempt_id == attempt_id and job.status in {
            "claimed",
            "running",
            "cancel_requested",
        }

    def _attempt_token_is_live(self, attempt_id: str, fencing_token: int) -> bool:
        attempt = self._repository.get_attempt(attempt_id)
        expected_digest = hashlib.sha256(str(fencing_token).encode()).hexdigest()
        return bool(
            attempt
            and attempt.status in {"claimed", "running"}
            and secrets.compare_digest(attempt.fencing_token_digest, expected_digest)
        )

    @classmethod
    def _checkpoint_ref_from_mapping(cls, value: Any) -> str | None:
        if value is None:
            return None
        checkpoint = cls._normalize_resume_checkpoint(value)
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(
                    checkpoint,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        if len(encoded) > 4096:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint reference exceeds its bound",
            )
        return f"{_CHECKPOINT_REF_PREFIX}{encoded}"

    @classmethod
    def _decode_checkpoint_ref(cls, value: str | None) -> dict[str, Any] | None:
        reference = str(value or "")
        if not reference.startswith(_CHECKPOINT_REF_PREFIX):
            return None
        encoded = reference.removeprefix(_CHECKPOINT_REF_PREFIX)
        if not encoded or len(encoded) > 4096:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "persisted resume checkpoint reference is invalid",
            )
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
            payload = json.loads(
                decoded,
                parse_constant=_reject_non_finite_json_constant,
            )
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "persisted resume checkpoint reference is invalid",
            ) from exc
        return cls._normalize_resume_checkpoint(payload)

    @staticmethod
    def _normalize_resume_checkpoint(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint must be an object",
            )
        if set(value) - {"relative_path", "binding"}:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint contains unknown fields",
            )
        supplied_path = value.get("relative_path")
        relative_path = supplied_path if isinstance(supplied_path, str) else ""
        binding = value.get("binding")
        if (
            not relative_path
            or len(relative_path) > 1024
            or relative_path.startswith(("/", "\\"))
            or ".." in Path(relative_path).parts
            or not isinstance(binding, Mapping)
        ):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint path or binding is invalid",
            )
        identifier_keys = ("job_id", "source_attempt_id")
        hash_keys = (
            "base_model_hash",
            "dataset_hash",
            "configuration_hash",
            "checkpoint_sha256",
        )
        if set(binding) != set(identifier_keys) | set(hash_keys):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint binding fields are invalid",
            )
        normalized_binding: dict[str, str] = {}
        for key in identifier_keys:
            supplied_child = binding.get(key)
            child = supplied_child if isinstance(supplied_child, str) else ""
            if not child or len(child) > 192:
                raise MlInternTrainingContractError(
                    "resume_checkpoint_invalid",
                    "resume checkpoint identity binding is invalid",
                )
            normalized_binding[key] = child
        for key in hash_keys:
            supplied_child = binding.get(key)
            child = supplied_child.lower() if isinstance(supplied_child, str) else ""
            if len(child) != 64 or any(character not in "0123456789abcdef" for character in child):
                raise MlInternTrainingContractError(
                    "resume_checkpoint_invalid",
                    "resume checkpoint hash binding is invalid",
                )
            normalized_binding[key] = child
        return {"relative_path": relative_path, "binding": normalized_binding}
