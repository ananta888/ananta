from agent.services.benchmark_job_service import BenchmarkJobService


def test_submit_hub_benchmark_job_records_completed_job(monkeypatch):
    service = BenchmarkJobService(max_workers=1)
    service._executor.submit = lambda fn, **kwargs: fn(**kwargs)  # type: ignore[method-assign]

    monkeypatch.setattr(
        "agent.services.benchmark_job_service.get_hub_benchmark_service",
        lambda: type(
            "FakeHubService",
            (),
            {"run_full_benchmark": lambda self, **kwargs: {"status": "completed", "total_tests": 3, "successful": 3}},
        )(),
    )

    job = service.submit_hub_benchmark_job(
        roles=["planner"],
        providers=["ollama"],
        max_execution_minutes=5,
        created_by="tester",
    )

    loaded = service.get_job(job["job_id"])
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert (loaded.get("summary") or {}).get("total_tests") == 3


def test_submit_ollama_benchmark_job_records_failed_job(monkeypatch):
    service = BenchmarkJobService(max_workers=1)
    service._executor.submit = lambda fn, **kwargs: fn(**kwargs)  # type: ignore[method-assign]

    monkeypatch.setattr(
        "agent.services.benchmark_job_service.get_ollama_benchmark_service",
        lambda: type(
            "FakeOllamaService",
            (),
            {"run_full_benchmark": lambda self, **kwargs: {"status": "failed", "summary": {"total_tests": 2, "failed": 2}}},
        )(),
    )

    job = service.submit_ollama_benchmark_job(
        models=["ananta-default"],
        roles=["coder"],
        parameter_variations=False,
        max_execution_minutes=5,
        base_url=None,
        created_by="tester",
    )

    loaded = service.get_job(job["job_id"])
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert (loaded.get("summary") or {}).get("failed") == 2
