from agent.llm_benchmarks import (
    record_benchmark_sample,
    recommend_model_for_context,
    recommend_models_for_context,
)


def test_recommend_model_for_context_prefers_matching_role_samples(tmp_path):
    data_dir = str(tmp_path)
    cfg = {}
    for _ in range(3):
        record_benchmark_sample(
            data_dir=data_dir,
            agent_cfg=cfg,
            provider="ollama",
            model="model-frontend",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=1200,
            tokens_total=900,
            cost_units=0.01,
            context_tags={"role_name": "Frontend Developer", "template_name": "ui-template"},
        )
    for _ in range(3):
        record_benchmark_sample(
            data_dir=data_dir,
            agent_cfg=cfg,
            provider="ollama",
            model="model-backend",
            task_kind="coding",
            success=False,
            quality_gate_passed=False,
            latency_ms=2400,
            tokens_total=1200,
            cost_units=0.02,
            context_tags={"role_name": "Backend Developer", "template_name": "api-template"},
        )

    rec = recommend_model_for_context(
        data_dir=data_dir,
        task_kind="coding",
        role_name="Frontend Developer",
        template_name="ui-template",
        min_samples=2,
    )
    assert rec is not None
    assert rec["model"] == "model-frontend"
    assert rec["selection_source"] == "benchmark_context_learning"


def test_recommend_models_for_context_returns_ranked_and_excludes_models(tmp_path):
    data_dir = str(tmp_path)
    cfg = {}
    for _ in range(4):
        record_benchmark_sample(
            data_dir=data_dir,
            agent_cfg=cfg,
            provider="ollama",
            model="best-model",
            task_kind="analysis",
            success=True,
            quality_gate_passed=True,
            latency_ms=500,
            tokens_total=400,
            cost_units=0.01,
            context_tags={"role_name": "Analyst", "template_name": "analysis-template"},
        )
    for _ in range(4):
        record_benchmark_sample(
            data_dir=data_dir,
            agent_cfg=cfg,
            provider="ollama",
            model="fallback-model",
            task_kind="analysis",
            success=True,
            quality_gate_passed=False,
            latency_ms=1800,
            tokens_total=1200,
            cost_units=0.04,
            context_tags={"role_name": "Analyst", "template_name": "analysis-template"},
        )

    ranked = recommend_models_for_context(
        data_dir=data_dir,
        task_kind="analysis",
        role_name="Analyst",
        template_name="analysis-template",
        min_samples=2,
        limit=2,
        exclude_models=["best-model"],
    )
    assert len(ranked) == 1
    assert ranked[0]["model"] == "fallback-model"
