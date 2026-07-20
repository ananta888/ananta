from __future__ import annotations

import time

from agent.repositories.speech_adaptation import SqlSpeechAdaptationDecisionStore
from agent.services.background.speech_adaptation_dispatcher import (
    HubSpeechAdaptationDispatcher,
)
from agent.services.speech_adaptation_job_service import (
    SpeechAdmissionDecision,
    SpeechPrincipal,
)
from agent.services.speech_adaptation_worker_port import SpeechWorkerSubmission
from ananta_contracts.speech_adaptation import SpeechAdaptationResult
from tests.speech_adaptation_support import digest, speech_job


class _Capacity:
    def __init__(self) -> None:
        self.released: list[str] = []

    def release(self, lease_id: str) -> None:
        self.released.append(lease_id)


class _Tasks:
    def __init__(self) -> None:
        self.finished: list[tuple[str, str, str]] = []

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        self.finished.append((task_id, status, reason_code))


class _Worker:
    def __init__(self, result) -> None:
        self.result_value = result
        self.submissions = 0
        self.polls = 0
        self.cancellations = 0

    def submit(self, job):
        self.submissions += 1
        return SpeechWorkerSubmission(job.job_id, job.attempt.attempt_id, "accepted")

    def result(self, _job):
        self.polls += 1
        return self.result_value

    def cancel(self, _job, *, reason_code):
        assert reason_code
        self.cancellations += 1


class _AcceptingService:
    def __init__(self, jobs: SqlSpeechAdaptationDecisionStore) -> None:
        self.jobs = jobs
        self.audit_transitions: list[str] = []

    def record_worker_transition(self, _principal, **values) -> None:
        self.audit_transitions.append(str(values["status"]))

    def accept_result(self, principal, job_id, result):
        row = self.jobs.transition_worker_state(
            job_id,
            expected_statuses=frozenset({"submitted", "running", "cancel_requested"}),
            status=result.status,
            reason_code=result.reason_code or f"speech_training_{result.status}",
            result=result,
        )
        from agent.services.speech_adaptation_job_service import restore_speech_adaptation_job

        return SpeechAdmissionDecision(
            row.id,
            row.task_id,
            row.status,
            row.reason_code,
            restore_speech_adaptation_job(dict(row.contract_payload)),
            row.request_digest,
        )


class _PromotingService(_AcceptingService):
    def __init__(self, jobs, job) -> None:
        super().__init__(jobs)
        self.job = job

    def promote_waiting(self, principal, job_id):
        current = self.jobs.get(principal, job_id)
        assert current is not None
        return self.jobs.replace(
            principal,
            SpeechAdmissionDecision(
                current.job_id,
                current.task_id,
                "queued",
                "speech_training_admitted",
                self.job,
                current.request_digest,
                current.admission_request,
            ),
            expected_statuses=frozenset({"queued"}),
        )


def _result(job, *, status: str = "completed") -> SpeechAdaptationResult:
    completed = status == "completed"
    return SpeechAdaptationResult.from_mapping(
        {
            "contract_version": "ananta.speech-adaptation.v1",
            "result_type": "speech_adaptation_result",
            "job_id": job.job_id,
            "attempt_id": job.attempt.attempt_id,
            "binding_digest": job.binding_digest,
            "fencing_digest": job.fencing.fencing_digest,
            "status": status,
            "events_digest": digest(f"events-{status}"),
            "evaluation_report_digest": digest("evaluation") if completed else None,
            "checkpoint_digest": digest("checkpoint") if completed else None,
            "artifact": (
                {
                    "artifact_id": job.artifact_target.target_id,
                    "artifact_ref": job.artifact_target.artifact_ref,
                    "sha256": digest("artifact"),
                    "size_bytes": 64,
                    "media_type": "application/vnd.ananta.speech-adapter",
                }
                if completed
                else None
            ),
            "reason_code": None if completed else "speech_training_cancelled",
        }
    )


def _persist(jobs, *, now_ms: int, suffix: str = "main"):
    job = speech_job(
        now_ms=now_ms,
        job_id=f"speech-job-dispatch-{suffix}",
        artifact_id=f"speech-adapter-dispatch-{suffix}",
    )
    principal = SpeechPrincipal(f"tenant-{suffix}", f"owner-{suffix}")
    decision = SpeechAdmissionDecision(
        job.job_id,
        f"speech-task-{suffix}",
        "queued",
        "speech_training_admitted",
        job,
        digest(f"request-{suffix}"),
    )
    jobs.create(
        principal,
        idempotency_digest=digest(f"idempotency-{suffix}"),
        decision=decision,
    )
    return principal, job


def test_dispatcher_recovers_persisted_submission_and_collects_result(app) -> None:
    del app
    now = time.time_ns() // 1_000_000
    jobs = SqlSpeechAdaptationDecisionStore()
    _principal, job = _persist(jobs, now_ms=now)
    worker = _Worker(None)
    capacity = _Capacity()
    tasks = _Tasks()
    clock = [now]
    first_service = _AcceptingService(jobs)
    first_process = HubSpeechAdaptationDispatcher(
        jobs=jobs,
        service=first_service,
        worker=worker,
        capacity=capacity,
        tasks=tasks,
        feature_enabled=lambda: True,
        clock_ms=lambda: clock[0],
    )
    submitted = first_process.run_once()
    assert submitted.submitted == 1
    assert jobs.get_row(job.job_id).status == "submitted"

    # A reconstructed dispatcher observes the same durable row and polls it.
    clock[0] += 1_000
    second_service = _AcceptingService(jobs)
    second_process = HubSpeechAdaptationDispatcher(
        jobs=SqlSpeechAdaptationDecisionStore(),
        service=second_service,
        worker=worker,
        capacity=capacity,
        tasks=tasks,
        feature_enabled=lambda: True,
        clock_ms=lambda: clock[0],
    )
    pending = second_process.run_once()
    assert pending.running == 1
    assert jobs.get_row(job.job_id).status == "running"

    worker.result_value = _result(job)
    clock[0] += 1_000
    completed = second_process.run_once()
    assert completed.completed == 1
    assert jobs.get_row(job.job_id).status == "completed"
    assert {"dispatching", "submitted"} <= set(first_service.audit_transitions)
    assert "running" in second_service.audit_transitions


def test_dispatcher_feature_kill_switch_fences_without_worker_publish(app) -> None:
    del app
    now = time.time_ns() // 1_000_000
    jobs = SqlSpeechAdaptationDecisionStore()
    _principal, job = _persist(jobs, now_ms=now, suffix="disabled")
    worker = _Worker(None)
    capacity = _Capacity()
    tasks = _Tasks()
    dispatcher = HubSpeechAdaptationDispatcher(
        jobs=jobs,
        service=_AcceptingService(jobs),
        worker=worker,
        capacity=capacity,
        tasks=tasks,
        feature_enabled=lambda: False,
        clock_ms=lambda: now,
    )
    summary = dispatcher.run_once()
    assert summary.cancelled == 1
    assert jobs.get_row(job.job_id).status == "cancelled"
    assert worker.submissions == 0
    assert capacity.released == [job.fencing.lease_id]
    assert tasks.finished[0][1:] == (
        "cancelled",
        "speech_adaptation_feature_disabled",
    )


def test_dispatcher_promotes_persisted_capacity_wait_before_submission(app) -> None:
    del app
    now = time.time_ns() // 1_000_000
    job = speech_job(
        now_ms=now,
        job_id="speech-job-capacity-wait",
        artifact_id="speech-adapter-capacity-wait",
    )
    principal = SpeechPrincipal("tenant-capacity-wait", "owner-capacity-wait")
    waiting = SpeechAdmissionDecision(
        job.job_id,
        "speech-task-capacity-wait",
        "queued",
        "speech_capacity_unavailable",
        None,
        digest("request-capacity-wait"),
        {"deadline_at_ms": now + 60_000},
    )
    jobs = SqlSpeechAdaptationDecisionStore()
    jobs.create(
        principal,
        idempotency_digest=digest("idempotency-capacity-wait"),
        decision=waiting,
    )
    worker = _Worker(None)
    dispatcher = HubSpeechAdaptationDispatcher(
        jobs=jobs,
        service=_PromotingService(jobs, job),
        worker=worker,
        capacity=_Capacity(),
        tasks=_Tasks(),
        feature_enabled=lambda: True,
        clock_ms=lambda: now,
    )

    summary = dispatcher.run_once()

    assert summary.submitted == 1
    assert worker.submissions == 1
    assert jobs.get_row(job.job_id).status == "submitted"
    assert jobs.get_row(job.job_id).contract_payload["binding_digest"] == job.binding_digest
