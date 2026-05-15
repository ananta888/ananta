from agent.services.ollama_parallel_runtime_service import OllamaParallelRuntimeService


def test_ollama_parallel_capacity_respected():
    svc = OllamaParallelRuntimeService()
    decisions = [
        svc.acquire_slot(
            endpoint="http://ollama:11434",
            model="ananta-default:latest",
            max_parallel_requests=4,
            queue_limit=64,
            lease_seconds=60,
            backpressure="queue_then_reject",
        )
        for _ in range(8)
    ]

    active = [d for d in decisions if d.status == "active"]
    queued = [d for d in decisions if d.status == "queued"]
    assert len(active) == 4
    assert len(queued) == 4

    status = svc.get_status()
    key = "http://ollama:11434::ananta-default:latest"
    assert status[key]["active_count"] == 4
    assert status[key]["queued_count"] == 4


def test_release_and_cleanup():
    svc = OllamaParallelRuntimeService()
    d = svc.acquire_slot(
        endpoint="http://ollama:11434",
        model="m1",
        max_parallel_requests=1,
        queue_limit=1,
        lease_seconds=1,
    )
    assert d.status == "active"
    svc.release_slot(endpoint="http://ollama:11434", model="m1", lease_id=str(d.lease_id))
    status = svc.get_status()["http://ollama:11434::m1"]
    assert status["active_count"] == 0
    assert status["completed_count"] == 1
