from agent.repository import worker_slot_lease_repo
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService


def _request(**overrides):
    request = {
        "selected_worker_id": "w-priority",
        "selected_worker_kind": "native_ananta_worker",
        "selected_runtime_target_id": "rt-priority",
        "selected_runtime_kind": "docker_container",
        "worker_capacity": 2,
        "runtime_capacity": 2,
        "worker_queue_limit": 8,
        "slot_lease_seconds": 60,
    }
    request.update(overrides)
    return request


def test_reserve_reduces_background_capacity():
    svc = WorkerPoolSchedulerService()
    first = svc.acquire_for_job(request=_request(interactive_reserve=1))
    second = svc.acquire_for_job(request=_request(interactive_reserve=1))
    assert first.status == "active"
    assert second.status == "queued"
    assert second.reason_code == "worker_parallel_capacity_exhausted"
    svc.release_for_job(first.slot_lease_id)


def test_interactive_priority_uses_reserved_slot():
    svc = WorkerPoolSchedulerService()
    first = svc.acquire_for_job(request=_request(interactive_reserve=1))
    second = svc.acquire_for_job(request=_request(interactive_reserve=1, priority="interactive"))
    assert first.status == "active"
    assert second.status == "active"
    lease = worker_slot_lease_repo.get_by_id(str(second.slot_lease_id))
    assert lease is not None
    assert lease.lease_metadata["priority"] == "interactive"
    svc.release_for_job(first.slot_lease_id)
    svc.release_for_job(second.slot_lease_id)


def test_queued_interactive_job_gets_front_position():
    svc = WorkerPoolSchedulerService()
    first = svc.acquire_for_job(request=_request(interactive_reserve=0))
    second = svc.acquire_for_job(request=_request(interactive_reserve=0))
    interactive = svc.acquire_for_job(request=_request(interactive_reserve=0, priority="interactive"))
    assert first.status == "active"
    assert second.status == "active"
    assert interactive.status == "queued"
    assert interactive.queue_position == 1
    svc.release_for_job(first.slot_lease_id)
    svc.release_for_job(second.slot_lease_id)
