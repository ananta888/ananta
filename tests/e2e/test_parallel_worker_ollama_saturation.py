from agent.services.ollama_parallel_runtime_service import OllamaParallelRuntimeService
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService


def test_parallel_saturation_worker_and_ollama_caps():
    scheduler = WorkerPoolSchedulerService()
    req = {
        "selected_worker_id": "w-e2e",
        "selected_worker_kind": "native_ananta_worker",
        "selected_runtime_target_id": "rt-e2e",
        "selected_runtime_kind": "docker_container",
        "worker_capacity": 8,
        "runtime_capacity": 8,
        "worker_queue_limit": 16,
        "slot_lease_seconds": 60,
        "ollama_endpoint": "http://ollama:11434",
        "ollama_model": "ananta-default:latest",
        "ollama_max_parallel_requests": 4,
        "ollama_queue_limit": 64,
    }
    decisions = [scheduler.acquire_for_job(request=req) for _ in range(8)]
    active = [d for d in decisions if d.status == "active"]
    assert len(active) == 4


def test_parallel_non_llm_jobs_can_use_worker_capacity():
    scheduler = WorkerPoolSchedulerService()
    req = {
        "selected_worker_id": "w-non-llm",
        "selected_worker_kind": "native_ananta_worker",
        "selected_runtime_target_id": "rt-non-llm",
        "selected_runtime_kind": "docker_container",
        "worker_capacity": 8,
        "runtime_capacity": 8,
        "worker_queue_limit": 16,
        "slot_lease_seconds": 60,
    }
    decisions = [scheduler.acquire_for_job(request=req) for _ in range(8)]
    assert len([d for d in decisions if d.status == "active"]) == 8
