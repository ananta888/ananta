from agent.repository import worker_slot_lease_repo
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService


def test_scheduler_acquire_and_release_worker_only():
    svc = WorkerPoolSchedulerService()
    decision = svc.acquire_for_job(
        request={
            "selected_worker_id": "w1",
            "selected_worker_kind": "native_ananta_worker",
            "selected_runtime_target_id": "rt1",
            "selected_runtime_kind": "docker_container",
            "worker_capacity": 2,
            "runtime_capacity": 2,
            "worker_queue_limit": 4,
            "slot_lease_seconds": 60,
        }
    )
    assert decision.status == "active"
    assert decision.slot_lease_id

    lease = worker_slot_lease_repo.get_by_id(str(decision.slot_lease_id))
    assert lease is not None
    assert lease.status == "active"

    svc.release_for_job(decision.slot_lease_id)
    lease2 = worker_slot_lease_repo.get_by_id(str(decision.slot_lease_id))
    assert lease2 is not None
    assert lease2.status == "released"


def test_scheduler_respects_ollama_capacity_and_queues():
    svc = WorkerPoolSchedulerService()
    req = {
        "selected_worker_id": "w-ollama",
        "selected_worker_kind": "native_ananta_worker",
        "selected_runtime_target_id": "rt-ollama",
        "selected_runtime_kind": "docker_container",
        "worker_capacity": 8,
        "runtime_capacity": 8,
        "worker_queue_limit": 8,
        "slot_lease_seconds": 60,
        "ollama_endpoint": "http://ollama:11434",
        "ollama_model": "model-x",
        "ollama_max_parallel_requests": 4,
        "ollama_queue_limit": 8,
    }
    decisions = [svc.acquire_for_job(request=req) for _ in range(5)]
    active = [d for d in decisions if d.status == "active"]
    queued = [d for d in decisions if d.status == "queued"]
    assert len(active) == 4
    assert len(queued) == 1


def test_scheduler_revalidation_blocks_stale_policy_decision():
    svc = WorkerPoolSchedulerService()
    decision = svc.acquire_for_job(
        request={
            "selected_worker_id": "w-rev",
            "selected_worker_kind": "native_ananta_worker",
            "selected_runtime_target_id": "rt-rev",
            "selected_runtime_kind": "docker_container",
            "worker_capacity": 1,
            "runtime_capacity": 1,
            "worker_queue_limit": 8,
            "slot_lease_seconds": 60,
        }
    )
    # saturate so next lease is queued
    queued = svc.acquire_for_job(
        request={
            "selected_worker_id": "w-rev",
            "selected_worker_kind": "native_ananta_worker",
            "selected_runtime_target_id": "rt-rev",
            "selected_runtime_kind": "docker_container",
            "worker_capacity": 1,
            "runtime_capacity": 1,
            "worker_queue_limit": 8,
            "slot_lease_seconds": 60,
            "policy_decision_ref": "p-old",
            "policy_decision_hash": "h-old",
        }
    )
    assert queued.status == "queued"
    reval = svc.revalidate_queued_job(
        slot_lease_id=str(queued.slot_lease_id),
        policy_decision_ref="p-new",
        policy_decision_hash="h-new",
        worker_online=True,
        policy_allowed=True,
        capacity_available=True,
    )
    assert reval.status == "rejected"
    assert reval.reason_code == "stale_policy_decision"
    svc.release_for_job(decision.slot_lease_id)
