from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

import pytest

from agent.db_models import MlInternDatasetDB
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.repository import worker_job_repo
from agent.services.ml_intern_training_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from ananta_contracts.unsloth_capability import compose_worker_capability_probe

ROOT = Path(__file__).resolve().parents[1]


class InlineExecutor:
    def submit(self, callback, *args):
        callback(*args)
        return None


class HoldingExecutor:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, callback, *args):
        self.calls.append((callback, args))
        return None


class FakeTaskQueue:
    def __init__(self) -> None:
        self.tasks: list[dict] = []

    def ingest_task(self, **values) -> None:
        self.tasks.append(values)


class FailingTaskQueue:
    def ingest_task(self, **values) -> None:
        del values
        raise RuntimeError("queue unavailable")


class ForcedCancelledExecutionPort:
    def capability_probe(self) -> dict:
        return compose_worker_capability_probe(
            contract_version="lora-training.v1",
            resource_profile="mock",
            active_gpu_profile="none",
            backend_availability={"mock": (True, None)},
            package_versions={},
            hardware={"cuda_available": False},
            runtime_ready=True,
        )

    def supports(self, **_values) -> bool:
        return True

    def execute(self, **_values) -> dict:
        return {"status": "cancelled", "cancelled": True, "cancel_mode": "forced"}


def _principal() -> MlInternTrainingPrincipal:
    suffix = uuid.uuid4().hex
    return MlInternTrainingPrincipal(f"tenant-{suffix}", f"admin-{suffix}")


def _principal_for(tenant_id: str) -> MlInternTrainingPrincipal:
    return MlInternTrainingPrincipal(tenant_id, f"admin-{uuid.uuid4().hex}")


def _payload(dataset_id: str) -> dict:
    return {
        "dataset_id": dataset_id,
        "job_type": "train_lora",
        "mode": "dry_run",
        "backend": "mock",
        "base_model": "local-test-model",
        "output_name": "adapter-test",
        "hyperparameters": {"max_steps": 1, "lora_rank": 8},
    }


def _create_dataset(
    repository: MlInternTrainingRepository,
    principal: MlInternTrainingPrincipal,
    path: Path,
    *,
    metadata: dict | None = None,
) -> MlInternDatasetDB:
    path.write_text('{"instruction":"hello","output":"world"}\n', encoding="utf-8")
    dataset, _ = repository.create_dataset(
        MlInternDatasetDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            name=path.name,
            status="ready",
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
            record_count=1,
            train_record_count=1,
            storage_ref=str(path),
            train_storage_ref=str(path),
            validation_report={"ok": True, "accepted_record_count": 1},
            dataset_metadata=dict(metadata or {}),
        )
    )
    return dataset


def test_async_create_materializes_job_task_events_and_result(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "train.jsonl")
    task_queue = FakeTaskQueue()
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: task_queue,
    )
    service = MlInternTrainingControlService(
        {
            "enabled": True,
            "artifact_root": str(tmp_path / "artifacts"),
            "require_dataset_validation": True,
            "max_concurrent_jobs": 16,
        },
        repository=repository,
        executor=InlineExecutor(),
    )
    accepted, replayed = service.create_job(principal, _payload(dataset.id), idempotency_key="unique-key-123")
    assert replayed is False
    assert accepted["poll_url"].endswith(accepted["id"])
    assert task_queue.tasks[0]["extra_fields"]["required_capabilities"] == [
        "lora_training",
        "mock",
        "device:cpu",
        "gpu_profile:none",
    ]
    detail = service.get_job(principal, accepted["id"])
    assert detail["status"] == "completed"
    assert detail["result_ref"].startswith("training-result:")
    assert detail["worker_job_id"] != detail["id"]
    projected = worker_job_repo.get_by_id(detail["worker_job_id"])
    assert projected is not None
    assert projected.parent_task_id == detail["task_id"]
    assert projected.status == "completed"
    assert projected.job_metadata["domain"] == "ml_intern_training"
    assert len(projected.job_metadata["tenant_scope_digest"]) == 64
    assert principal.tenant_id not in str(projected.job_metadata)
    assert principal.subject not in str(projected.job_metadata)
    events = service.list_events(principal, accepted["id"])
    assert [item["type"] for item in events["items"]] == [
        "job_queued",
        "claimed",
        "running",
        "completed",
    ]


def test_admit_job_defers_execution_until_explicit_hub_dispatch(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "deferred-dispatch.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    holding = HoldingExecutor()
    service = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 1},
        repository=repository,
        executor=holding,
    )

    accepted, replayed = service.admit_job(
        principal,
        _payload(dataset.id),
        idempotency_key="deferred-dispatch",
    )

    assert replayed is False
    assert service.get_job(principal, accepted["id"])["status"] == "queued"
    assert holding.calls == []

    assert service.schedule_reconciled_job(principal, accepted["id"]) is True
    assert len(holding.calls) == 1


def test_training_route_audit_precedes_automatic_hub_dispatch() -> None:
    source = (ROOT / "agent/routes/ml_intern_training.py").read_text(encoding="utf-8")
    admission = source.index("services.control.admit_job(")
    audit = source.index('"ml_intern_training_job_admitted"', admission)
    deferred = source.index("_DEFERRED_TRAINING_DISPATCH,", audit)
    teardown = source.index("def _dispatch_admitted_training_job(")
    dispatch = source.index("control.schedule_reconciled_job(", teardown)

    assert admission < audit < deferred
    assert teardown < dispatch


def test_idempotent_replay_does_not_enqueue_twice(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "replay.jsonl")
    task_queue = FakeTaskQueue()
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: task_queue,
    )
    holding = HoldingExecutor()
    service = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 16}, repository=repository, executor=holding
    )
    first, _ = service.create_job(principal, _payload(dataset.id), idempotency_key="same-key-123")
    second, replayed = service.create_job(principal, _payload(dataset.id), idempotency_key="same-key-123")
    assert replayed is True
    assert second["id"] == first["id"]
    assert len(task_queue.tasks) == 1
    assert len(holding.calls) == 1


def test_task_materialization_failure_leaves_terminal_auditable_tombstone(
    app, tmp_path, monkeypatch
) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "task-failure.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FailingTaskQueue(),
    )
    service = MlInternTrainingControlService(
        {"enabled": True}, repository=repository, executor=HoldingExecutor()
    )

    with pytest.raises(MlInternTrainingContractError) as failure:
        service.create_job(principal, _payload(dataset.id), idempotency_key="task-failure")

    assert failure.value.reason_code == "task_materialization_failed"
    jobs = repository.list_jobs(principal, limit=10, offset=0)
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].error_code == "task_materialization_failed"
    events = repository.list_events(principal, jobs[0].id, after_sequence=0, limit=10)
    assert [event.event_type for event in events] == ["failed"]


def test_queued_job_can_be_cancelled_and_is_tenant_scoped(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "cancel.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    service = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 16}, repository=repository, executor=HoldingExecutor()
    )
    accepted, _ = service.create_job(principal, _payload(dataset.id), idempotency_key="cancel-key-123")
    cancelled = service.cancel_job(
        principal,
        accepted["id"],
        idempotency_key="cancel-request-123",
        reason="operator requested cancellation",
    )
    replayed = service.cancel_job(
        principal,
        accepted["id"],
        idempotency_key="cancel-request-123",
        reason="operator requested cancellation",
    )
    assert cancelled["status"] == "cancel_requested"
    assert replayed["version"] == cancelled["version"]
    with pytest.raises(MlInternTrainingContractError) as missing:
        service.get_job(MlInternTrainingPrincipal("other", "other"), accepted["id"])
    assert missing.value.status_code == 404


def test_worker_forced_cancel_mode_reaches_hub_detail_and_event(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset_path = tmp_path / "forced-cancel.jsonl"
    dataset = _create_dataset(repository, principal, dataset_path)
    dataset.validation_storage_ref = str(dataset_path)
    dataset = repository.save_dataset(dataset, expected_version=dataset.version)
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    service = MlInternTrainingControlService(
        {
            "enabled": True,
            "mode": "live",
            "allowed_job_types": ["train_lora"],
            "base_models": ["local-test-model"],
            "max_concurrent_jobs": 1,
        },
        repository=repository,
        executor=InlineExecutor(),
        execution_port=ForcedCancelledExecutionPort(),
    )
    payload = {
        **_payload(dataset.id),
        "mode": "live",
        "live_confirmed": True,
        "risk_reason": "forced cancel propagation test",
    }

    accepted, _ = service.create_job(principal, payload, idempotency_key="forced-cancel")
    detail = service.get_job(principal, accepted["id"])
    events = service.list_events(principal, accepted["id"])["items"]

    assert detail["status"] == "cancelled"
    assert detail["cancel_mode"] == "forced"
    assert detail["result"]["cancel_mode"] == "forced"
    assert events[-1]["payload"]["cancel_mode"] == "forced"


def test_live_training_requires_valid_dataset_and_worker(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "live.jsonl")
    dataset.validation_report = {}
    repository.save_dataset(dataset, expected_version=dataset.version)
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    service = MlInternTrainingControlService({"enabled": True, "mode": "live"}, repository=repository)
    payload = {
        **_payload(dataset.id),
        "mode": "live",
        "backend": "peft_trl",
        "live_confirmed": True,
        "risk_reason": "validation admission policy test",
    }
    with pytest.raises(MlInternTrainingContractError) as invalid:
        service.create_job(principal, payload, idempotency_key="live-key-123")
    assert invalid.value.reason_code == "dataset_validation_required"


def test_capacity_admits_bounded_waiting_queue_and_reports_position(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "bounded-queue.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    holding = HoldingExecutor()
    service = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 1, "max_queued_jobs": 1},
        repository=repository,
        executor=holding,
    )

    running, _ = service.create_job(principal, _payload(dataset.id), idempotency_key="capacity-running")
    waiting, _ = service.create_job(principal, _payload(dataset.id), idempotency_key="capacity-waiting")

    assert running["queue_position"] is None
    assert waiting["queue_position"] == 1
    assert len(holding.calls) == 1
    with pytest.raises(MlInternTrainingContractError) as exhausted:
        service.create_job(principal, _payload(dataset.id), idempotency_key="capacity-overflow")
    assert exhausted.value.reason_code == "training_capacity_exhausted"
    assert exhausted.value.status_code == 429


def test_scheduler_round_robins_tenants_and_dispatches_next_after_terminal(
    app,
    tmp_path,
    monkeypatch,
) -> None:
    del app
    repository = MlInternTrainingRepository()
    tenant_a = _principal_for(f"tenant-a-{uuid.uuid4().hex}")
    tenant_b = _principal_for(f"tenant-b-{uuid.uuid4().hex}")
    dataset_a = _create_dataset(repository, tenant_a, tmp_path / "tenant-a.jsonl")
    dataset_b = _create_dataset(repository, tenant_b, tmp_path / "tenant-b.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    holding = HoldingExecutor()
    service = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 1, "max_queued_jobs": 8},
        repository=repository,
        executor=holding,
    )

    first_a, _ = service.create_job(tenant_a, _payload(dataset_a.id), idempotency_key="fair-a-first")
    second_a, _ = service.create_job(tenant_a, _payload(dataset_a.id), idempotency_key="fair-a-second")
    first_b, _ = service.create_job(tenant_b, _payload(dataset_b.id), idempotency_key="fair-b-first")

    assert service.get_job(tenant_b, first_b["id"])["queue_position"] == 1
    assert service.get_job(tenant_a, second_a["id"])["queue_position"] == 2
    callback, args = holding.calls[0]
    assert args[1] == first_a["id"]
    callback(*args)

    assert len(holding.calls) == 2
    _next_callback, next_args = holding.calls[1]
    assert next_args[1] == first_b["id"]
    assert service.get_job(tenant_b, first_b["id"])["queue_position"] is None
    assert service.get_job(tenant_a, second_a["id"])["queue_position"] == 1


def test_concurrent_hub_replicas_cannot_double_claim_one_job(app, tmp_path, monkeypatch) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "claim-race.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    first_executor = HoldingExecutor()
    second_executor = HoldingExecutor()
    first = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 1},
        repository=repository,
        executor=first_executor,
    )
    second = MlInternTrainingControlService(
        {"enabled": True, "max_concurrent_jobs": 1},
        repository=repository,
        executor=second_executor,
    )
    accepted, _ = first.create_job(
        principal,
        _payload(dataset.id),
        idempotency_key="concurrent-claim",
    )
    assert second.schedule_reconciled_job(principal, accepted["id"]) is True
    assert len(first_executor.calls) == len(second_executor.calls) == 1

    threads = [
        threading.Thread(target=callback, args=args)
        for callback, args in (first_executor.calls[0], second_executor.calls[0])
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    job = repository.get_job(principal, accepted["id"])
    attempts = repository.list_attempts(accepted["id"])
    assert job is not None and job.status == "completed"
    assert len(attempts) == 1
    assert attempts[0].status == "completed"


def test_two_hub_replicas_share_one_execution_slot_for_different_jobs(
    app, tmp_path, monkeypatch
) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(repository, principal, tmp_path / "replica-capacity.jsonl")
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    first_executor = HoldingExecutor()
    second_executor = HoldingExecutor()
    config = {"enabled": True, "max_concurrent_jobs": 1, "max_queued_jobs": 2}
    first = MlInternTrainingControlService(config, repository=repository, executor=first_executor)
    second = MlInternTrainingControlService(config, repository=repository, executor=second_executor)
    first_job, _ = first.create_job(principal, _payload(dataset.id), idempotency_key="replica-first")
    second_job, _ = second.create_job(principal, _payload(dataset.id), idempotency_key="replica-second")

    assert len(first_executor.calls) == len(second_executor.calls) == 1
    entered: list[str] = []
    entered_lock = threading.Lock()
    release = threading.Event()

    def blocking_execute(job, _dataset_path):
        with entered_lock:
            entered.append(job.id)
        release.wait(timeout=5)
        return {"status": "completed", "result_ref": f"training-result:{job.id}"}

    first._execute_local_bounded = blocking_execute  # type: ignore[method-assign]
    second._execute_local_bounded = blocking_execute  # type: ignore[method-assign]
    threads = [
        threading.Thread(target=callback, args=args)
        for callback, args in (first_executor.calls[0], second_executor.calls[0])
    ]
    for thread in threads:
        thread.start()
    for _ in range(100):
        with entered_lock:
            if entered:
                break
        threading.Event().wait(0.01)
    with entered_lock:
        assert entered == [first_job["id"]]
    assert sum(
        attempt.status in {"claimed", "running"}
        for job_id in (first_job["id"], second_job["id"])
        for attempt in repository.list_attempts(job_id)
    ) == 1

    release.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    queued_callbacks = first_executor.calls[1:] + second_executor.calls[1:]
    assert len(queued_callbacks) == 1
    callback, args = queued_callbacks[0]
    callback(*args)
    assert repository.get_job(principal, first_job["id"]).status == "completed"
    assert repository.get_job(principal, second_job["id"]).status == "completed"


def test_capabilities_compose_optional_unsloth_facets_unavailable_by_default() -> None:
    service = MlInternTrainingControlService({"enabled": True})
    capabilities = service.capabilities()
    backend_status = {item["id"]: item for item in capabilities["backends"]}
    facets = {
        item["id"]: item
        for item in capabilities["unsloth_capabilities"]["facets"]
    }

    for backend in ("unsloth_vision", "unsloth_audio", "unsloth_embedding"):
        assert backend_status[backend]["available"] is False
    assert facets["training.vision"]["available"] is False
    assert facets["training.audio"]["available"] is False
    assert facets["training.embedding"]["available"] is False
    assert len(capabilities["unsloth_capabilities"]["snapshot_id"]) == 64


def test_create_job_binds_canonical_dataset_and_verified_source_run_provenance(
    app, tmp_path, monkeypatch
) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    source_id = "SRC_training-corpus"
    run_id = "RUN_materialization-1"
    dataset = _create_dataset(
        repository,
        principal,
        tmp_path / "grounded.jsonl",
        metadata={
            "source_ids": [source_id],
            "run_ids": [run_id],
            "provenance_verified": True,
        },
    )
    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.get_task_queue_service",
        lambda: FakeTaskQueue(),
    )
    service = MlInternTrainingControlService(
        {
            "enabled": True,
            "unsloth_security": {
                "require_grounded_provenance": True,
                "trusted_source_ids": [source_id],
                "trusted_run_ids": [run_id],
            },
        },
        repository=repository,
        executor=HoldingExecutor(),
    )
    accepted, _ = service.create_job(
        principal,
        {
            **_payload(dataset.id),
            "source_ids": [source_id],
            "run_ids": [run_id],
        },
        idempotency_key="grounded-provenance",
    )
    persisted = repository.get_job(principal, accepted["id"])
    assert persisted.request_spec["dataset_hash"] == dataset.content_sha256
    assert persisted.request_spec["source_ids"] == [source_id]
    assert persisted.request_spec["run_ids"] == [run_id]
    assert persisted.request_spec["provenance_status"] == "verified"


def test_create_job_rejects_source_id_not_bound_to_dataset(app, tmp_path) -> None:
    del app
    repository = MlInternTrainingRepository()
    principal = _principal()
    dataset = _create_dataset(
        repository,
        principal,
        tmp_path / "unverified-source.jsonl",
        metadata={
            "source_ids": ["SRC_bound"],
            "run_ids": ["RUN_bound"],
            "provenance_verified": True,
        },
    )
    service = MlInternTrainingControlService({"enabled": True}, repository=repository)
    with pytest.raises(MlInternTrainingContractError) as error:
        service.create_job(
            principal,
            {
                **_payload(dataset.id),
                "source_ids": ["SRC_unknown"],
                "run_ids": ["RUN_bound"],
            },
            idempotency_key="unknown-source",
        )
    assert error.value.reason_code == "source_id_unverified"
