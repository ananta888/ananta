from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.speech_reconciliation_scheduler import (
    QueuedSpeechReconciliation,
    SpeechReconciliationLease,
    SpeechReconciliationScheduler,
    SpeechReconciliationWorkerCandidate,
)
from agent.services.speech_reconciliation_task_port import HubSpeechReconciliationTaskPort
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationJob,
    SpeechResourceVector,
)


def _job(number: int = 1) -> SpeechReconciliationJob:
    return SpeechReconciliationJob(
        contract_version=CONTRACT_VERSION,
        job_id=f"speech-job-{number}",
        attempt_id=f"speech-attempt-{number}",
        fencing_token_digest=f"{number:064x}",
        fencing_epoch=number,
        consent_id=f"speech-consent-{number}",
        consent_version=1,
        revocation_epoch=0,
        input_manifest_digest=f"{number + 10:064x}",
        input_lineage_digest=f"{number + 20:064x}",
        input_artifact_ref=f"artifact://speech-evidence/manifest-{number}",
        policy_digest=f"{number + 30:064x}",
        research_policy_ref=None,
        source_duration_ms=60_000,
        max_compute_factor=10,
        ledger_sequence=0,
        key_epoch=1,
        deadline_at_ms=9_999_999_999_999,
        stage="staging",
    )


class _Queue:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def ingest_task(self, **values) -> None:
        self.rows.append(values)


class _Terminal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def cancel(self, task_id: str, *, reason_code: str) -> None:
        self.calls.append((task_id, reason_code))


class _Leases:
    def __init__(self) -> None:
        self.acquired: list[str] = []
        self.revoked: list[tuple[str, str]] = []

    def acquire(self, queued, candidate, *, ttl_ms):
        self.acquired.append(queued.job.job_id)
        return SpeechReconciliationLease(f"lease-{queued.job.job_id}", queued.job, candidate.worker_id, ttl_ms)

    def revoke(self, lease_id: str, *, reason_code: str) -> None:
        self.revoked.append((lease_id, reason_code))


def _resources(value: int = 1) -> SpeechResourceVector:
    return SpeechResourceVector(
        wall_time_ms=value,
        cpu_time_ms=value,
        gpu_time_ms=0,
        memory_byte_ms=value,
        disk_bytes=value,
        checkpoint_bytes=value,
        energy_millijoules=0,
    )


def _queued(job: SpeechReconciliationJob, tenant: str, sequence: int, priority: int = 50):
    return QueuedSpeechReconciliation(
        job=job,
        tenant_id=tenant,
        owner_subject=f"owner-{tenant}",
        priority=priority,
        queued_sequence=sequence,
        allowed_locations=frozenset({"local"}),
        requested_resources=_resources(),
    )


def _worker(**changes) -> SpeechReconciliationWorkerCandidate:
    values = {
        "worker_id": "worker-local",
        "location": "local",
        "capabilities": frozenset({"speech_reconciliation"}),
        "capacity": _resources(100),
        "max_offline_assignments": 4,
        "active_offline_assignments": 0,
    }
    values.update(changes)
    return SpeechReconciliationWorkerCandidate(**values)


def test_parent_and_child_are_hub_owned_content_free_and_non_orchestrating() -> None:
    queue = _Queue()
    terminal = _Terminal()
    port = HubSpeechReconciliationTaskPort(queue, terminal)
    job = _job()
    parent = port.materialize_parent(job, tenant_id="tenant-secret", owner_subject="owner-secret")
    child = port.enqueue_attempt(
        job,
        tenant_id="tenant-secret",
        owner_subject="owner-secret",
        worker_id="worker-local",
        worker_location="local",
        resource_profile=_resources().to_dict(),
        checkpoint_ref=None,
    )
    assert child.parent_task_id == parent.parent_task_id
    assert queue.rows[1]["extra_fields"]["parent_task_id"] == parent.parent_task_id
    context = queue.rows[1]["extra_fields"]["worker_execution_context"]["speech_reconciliation"]
    assert context["followup_task_creation_allowed"] is False
    assert context["peer_transfer_allowed"] is False
    assert context["training_delegation_allowed"] is False
    assert "tenant-secret" not in repr(context) and "owner-secret" not in repr(context)
    port.cancel(child.attempt_task_id or "", reason_code="speech_reconciliation_user_cancelled")
    assert terminal.calls == [(child.attempt_task_id, "speech_reconciliation_user_cancelled")]


def test_projection_accepts_a_status_capable_terminal_adapter() -> None:
    class _StatusTerminal:
        def __init__(self) -> None:
            self.calls = []

        def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
            self.calls.append((task_id, status, reason_code))

    terminal = _StatusTerminal()
    port = HubSpeechReconciliationTaskPort(_Queue(), terminal)

    port.finish(
        "speech-reconciliation-attempt-terminal",
        status="completed",
        reason_code="speech_reconciliation_dataset_materialized",
    )

    assert terminal.calls == [
        (
            "speech-reconciliation-attempt-terminal",
            "completed",
            "speech_reconciliation_dataset_materialized",
        )
    ]


def test_scheduler_is_tenant_fair_capability_bounded_and_yields_to_live_pressure() -> None:
    queue = _Queue()
    tasks = HubSpeechReconciliationTaskPort(queue)
    leases = _Leases()
    scheduler = SpeechReconciliationScheduler(
        leases=leases,
        tasks=tasks,
        max_offline_assignments=3,
    )
    jobs = (
        _queued(_job(1), "tenant-a", 1, 100),
        _queued(_job(2), "tenant-a", 2, 90),
        _queued(_job(3), "tenant-b", 3, 10),
    )
    assert scheduler.schedule(jobs, (_worker(),), live_pressure=True) == ()
    scheduled = scheduler.schedule(jobs, (_worker(),), live_pressure=False)
    assert len(scheduled) == 3
    assert leases.acquired == ["speech-job-1", "speech-job-3", "speech-job-2"]
    assert all(row["extra_fields"]["required_capabilities"] == ["speech_reconciliation"] for row in queue.rows)

    leases.acquired.clear()
    assert scheduler.schedule(jobs, (_worker(capabilities=frozenset()),), live_pressure=False) == ()
    assert not leases.acquired


def test_projection_failure_revokes_new_lease_and_invalid_priority_fails_closed() -> None:
    class _FailingTasks(HubSpeechReconciliationTaskPort):
        def enqueue_attempt(self, *args, **kwargs):
            raise RuntimeError("queue down")

    leases = _Leases()
    scheduler = SpeechReconciliationScheduler(leases=leases, tasks=_FailingTasks(_Queue()))
    with pytest.raises(RuntimeError, match="queue down"):
        scheduler.schedule((_queued(_job(), "tenant-a", 1),), (_worker(),), live_pressure=False)
    assert leases.revoked and leases.revoked[0][1].endswith("projection_failed")

    invalid = replace(_queued(_job(), "tenant-a", 1), priority=101)
    with pytest.raises(Exception, match="priority_invalid"):
        scheduler.schedule((invalid,), (_worker(),), live_pressure=False)
