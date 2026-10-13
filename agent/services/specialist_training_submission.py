"""Bounded specialist submission through the existing Hub training scheduler."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand


class SpecialistTrainingControlPort(Protocol):
    def create_job(
        self, principal: MlInternTrainingPrincipal, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> tuple[dict[str, Any], bool]: ...
    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> dict[str, Any]: ...
    def cancel_job(
        self, principal: MlInternTrainingPrincipal, job_id: str, *, idempotency_key: str, reason: str | None = None
    ) -> dict[str, Any]: ...


class SpecialistTrainingSubmissionError(RuntimeError):
    def __init__(self, reason_code: str, job_id: str | None = None):
        self.reason_code = reason_code
        self.job_id = job_id
        super().__init__(reason_code)


class HubSpecialistJobSubmission:
    """Return a completed job reference, never an unverified training outcome."""

    def __init__(
        self,
        *,
        control: SpecialistTrainingControlPort,
        job_type: str = "train_lora",
        principal: MlInternTrainingPrincipal,
        timeout_seconds: float = 3600,
        poll_interval_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        for value, maximum in ((timeout_seconds, 86400), (poll_interval_seconds, 10)):
            if type(value) not in (int, float) or not 0 < value <= maximum or not math.isfinite(value):
                raise ValueError("specialist_training_wait_configuration_invalid")
        if poll_interval_seconds < 0.05:
            raise ValueError("specialist_training_wait_configuration_invalid")
        if job_type not in {"train_lora", "evaluate_lora"}:
            raise ValueError("specialist_submission_job_type_invalid")
        self._job_type = job_type
        self._control = control
        self._principal = principal
        self._timeout = timeout_seconds
        self._interval = poll_interval_seconds
        self._clock = clock
        self._sleeper = sleeper

    def run(self, command: CreateTrainingJobCommand) -> str:
        spec = command.request_spec
        if (
            command.job_type != self._job_type
            or spec.get("task_family") != "specialist_decision"
            or type(spec.get("expected_dataset_version")) is not int
        ):
            raise SpecialistTrainingSubmissionError("specialist_training_admission_required")
        if self._job_type == "evaluate_lora" and (
            spec.get("scorer_name") != "specialist_decision"
            or not spec.get("adapter_id")
            or not spec.get("expected_adapter_sha256")
        ):
            raise SpecialistTrainingSubmissionError("specialist_evaluation_adapter_binding_required")
        kind = "train" if self._job_type == "train_lora" else "evaluate"
        digest = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        deadline = self._clock() + self._timeout
        accepted, _replayed = self._control.create_job(
            self._principal, spec, idempotency_key=f"specialist-{kind}-{digest}"
        )
        job_id = accepted.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise SpecialistTrainingSubmissionError("specialist_training_job_correlation_invalid")
        # The poll count also bounds deterministic clocks that stop advancing.
        waited = 0.0
        for _poll in range(min(100000, math.ceil(self._timeout / self._interval) + 1)):
            job = self._control.get_job(self._principal, job_id)
            if job.get("id") != job_id:
                raise SpecialistTrainingSubmissionError("specialist_training_job_correlation_invalid", job_id)
            state = job.get("status")
            if state == "completed":
                return job_id
            if state in {"failed", "cancelled"}:
                raise SpecialistTrainingSubmissionError(f"specialist_training_{state}", job_id)
            if state not in {"queued", "claimed", "running", "cancel_requested", "interrupted"}:
                raise SpecialistTrainingSubmissionError("specialist_training_status_invalid", job_id)
            remaining = min(deadline - self._clock(), self._timeout - waited)
            if remaining <= 0:
                break
            duration = min(self._interval, remaining)
            self._sleeper(duration)
            waited += duration
        self._control.cancel_job(
            self._principal,
            job_id,
            idempotency_key=f"specialist-timeout-{digest}",
            reason=f"specialist {kind} exceeded its bounded wait",
        )
        raise SpecialistTrainingSubmissionError("specialist_training_timeout", job_id)


# Backward-compatible name for the original training-only default configuration.
HubSpecialistTrainingSubmission = HubSpecialistJobSubmission
