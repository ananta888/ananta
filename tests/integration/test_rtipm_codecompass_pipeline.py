from __future__ import annotations

from agent.services.path_ai_mode_policy_service import PathAiModePolicyService, PathAiModeRule
from agent.services.pre_model_context_config import MODE_PREFER_CONTEXT
from agent.services.pre_model_context_orchestrator import DECISION_USE_CONTEXT, PreModelContextOrchestrator
from agent.services.restricted_model_inference_service import MockInferenceAdapter, RestrictedModelInferenceService


def test_rtipm_codecompass_pipeline_mock_reranks_and_traces() -> None:
    svc = RestrictedModelInferenceService(
        adapters=[MockInferenceAdapter()],
        policy_service=PathAiModePolicyService(),
        use_mock_fallback=False,
    )
    orc = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/a.py", "record_id": "a", "excerpt": "authentication token", "embedding_score": 0.1},
            {"path": "agent/b.py", "record_id": "b", "excerpt": "logging helper", "embedding_score": 0.9},
        ],
        restricted_inference_service=svc,
    )

    result = orc.orchestrate(
        task_text="authentication token",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "restricted_inference_rerank_enabled": True,
                "trace_scores": True,
                "fallback_without_model": True,
                "score_weights": {
                    "embedding_score": 0.0,
                    "graph_score": 0.0,
                    "symbol_score": 0.0,
                    "transformer_rerank_score": 1.0,
                    "policy_penalty": -0.2,
                },
            },
        },
    )

    assert result.decision == DECISION_USE_CONTEXT
    assert result.context_package is not None
    candidates = result.context_package.to_dict()["candidates"]
    assert candidates[0]["path"] == "agent/a.py"
    assert "score_trace" in candidates[0]


def test_rtipm_codecompass_pipeline_policy_block_falls_back_without_model_error() -> None:
    policy = PathAiModePolicyService(rules=[
        PathAiModeRule.from_raw({
            "path_glob": "agent/**",
            "allowed_ai_modes": ["codecompass_only"],
            "blocked_ai_modes": ["restricted_transformer_inference", "full_llm", "direct_llm"],
        })
    ])
    svc = RestrictedModelInferenceService(
        adapters=[MockInferenceAdapter()],
        policy_service=policy,
        use_mock_fallback=False,
    )
    orc = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/a.py", "record_id": "a", "excerpt": "authentication token", "embedding_score": 0.9},
        ],
        restricted_inference_service=svc,
        path_policy_service=policy,
    )

    result = orc.orchestrate(
        task_text="authentication token",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "restricted_inference_rerank_enabled": True,
                "fallback_without_model": True,
            },
        },
    )

    assert result.decision == DECISION_USE_CONTEXT
    assert result.trace is not None
    assert any(event.event == "restricted_rerank_policy_blocked" for event in result.trace.events)
