from agent.db_models import WorkerJobDB
from agent.repository import worker_job_repo, worker_slot_lease_repo
from agent.services.worker_job_service import WorkerJobService
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService


def test_create_worker_job_persists_scheduling_fields():
    service = WorkerJobService()
    job = service.create_worker_job(
        parent_task_id="p1",
        subtask_id="s1",
        worker_url="http://worker:5000",
        context_bundle_id=None,
        allowed_tools=["read"],
        expected_output_schema={"type": "object"},
        scheduling_decision={
            "slot_lease_id": "lease-1",
            "queue_position": 2,
            "scheduled_ollama_endpoint": "http://ollama:11434",
            "scheduled_ollama_model": "ananta-default:latest",
            "parallel_group_id": "group-a",
            "reason_code": "queued",
            "status": "queued",
        },
    )
    loaded = worker_job_repo.get_by_id(job.id)
    assert loaded is not None
    assert loaded.slot_lease_id == "lease-1"
    assert loaded.queue_position == 2
    assert loaded.scheduled_ollama_model == "ananta-default:latest"


def test_record_worker_result_releases_slot(monkeypatch):
    scheduler = WorkerPoolSchedulerService()
    decision = scheduler.acquire_for_job(
        request={
            "selected_worker_id": "w2",
            "selected_worker_kind": "native_ananta_worker",
            "selected_runtime_target_id": "rt2",
            "selected_runtime_kind": "docker_container",
            "worker_capacity": 2,
            "runtime_capacity": 2,
            "slot_lease_seconds": 60,
        }
    )
    service = WorkerJobService()
    job = worker_job_repo.save(
        WorkerJobDB(
            parent_task_id="p2",
            subtask_id="s2",
            worker_url="http://worker:5000",
            status="running",
            slot_lease_id=decision.slot_lease_id,
        )
    )
    service.record_worker_result(
        worker_job_id=job.id,
        task_id="s2",
        worker_url="http://worker:5000",
        status="completed",
        output="ok",
    )
    lease = worker_slot_lease_repo.get_by_id(str(decision.slot_lease_id))
    assert lease is not None
    assert lease.status == "released"
