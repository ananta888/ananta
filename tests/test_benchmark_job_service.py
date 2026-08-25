from types import SimpleNamespace

from agent.services.benchmark_job_service import BenchmarkJobService
from agent.services.cognitive_style_benchmark_service import CognitiveStyleBenchmarkSuite
from agent.services.cognitive_style_rebenchmark_service import (
    CognitiveStyleRebenchmarkWorkItem,
)
from agent.services.model_profile_loader import ModelProfile
from ananta_contracts.cognitive_style import StyleMeasurementContext


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


def test_style_rebenchmark_batch_advances_revision_serially(monkeypatch):
    service = BenchmarkJobService(max_workers=1)
    service._executor.submit = lambda fn, **kwargs: fn(**kwargs)  # type: ignore[method-assign]
    outcomes = []
    monkeypatch.setattr(service, "_observe_style_benchmark", outcomes.append)

    revisions = []

    class _Styles:
        def record_benchmark_result(self, result, *, expected_revision):
            del result
            revisions.append(expected_revision)
            return SimpleNamespace(revision=expected_revision + 1)

    monkeypatch.setattr(
        "agent.services.cognitive_style_service.get_cognitive_style_service",
        lambda: _Styles(),
    )
    monkeypatch.setattr(
        "agent.services.cognitive_style_benchmark_service.CognitiveStyleBenchmarkService.run",
        lambda self, *, profile, plan: SimpleNamespace(
            profile=SimpleNamespace(profile_id=f"style-{profile.profile_id}"),
            observations=(1, 2),
        ),
    )

    def item(profile_id):
        context = StyleMeasurementContext(
            model_profile_id=profile_id,
            model_revision="r1",
            quantization="q8",
            runtime="llamacpp",
            backend_id="lmstudio",
            system_prompt_digest="sha256:system",
            role_prompt_digest="sha256:role",
            tool_mode="native_tools",
            sampling_digest="sha256:sampling",
        )
        return CognitiveStyleRebenchmarkWorkItem(
            profile=ModelProfile(
                profile_id=profile_id, provider_id="lmstudio", model=profile_id,
            ),
            plan=CognitiveStyleBenchmarkSuite.plan(context),
        )

    job = service.submit_cognitive_style_rebenchmark_job(
        work_items=(item("one"), item("two")),
        expected_revision=4,
        created_by="tester",
    )

    loaded = service.get_job(job["job_id"])
    assert loaded["status"] == "completed"
    assert loaded["result"]["configuration_revision"] == 6
    assert revisions == [4, 5]
    assert outcomes == ["completed"]
