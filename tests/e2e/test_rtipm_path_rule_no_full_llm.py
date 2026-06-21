from __future__ import annotations

import pytest

from agent.services.path_ai_mode_policy_service import (
    AI_MODE_DIRECT_LLM,
    AI_MODE_FULL_LLM,
    AI_MODE_CODECOMPASS_ONLY,
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    PathAiModeRule,
)
from agent.services.pre_model_context_config import MODE_PREFER_CONTEXT
from agent.services.pre_model_context_orchestrator import DECISION_USE_CONTEXT, PreModelContextOrchestrator
from agent.services.restricted_model_inference_service import MockInferenceAdapter, RestrictedModelInferenceService


@pytest.mark.integration
def test_path_rule_blocks_full_llm_but_allows_codecompass_rtipm_context() -> None:
    policy = PathAiModePolicyService(rules=[
        PathAiModeRule.from_raw({
            "path_glob": "agent/services/**",
            "allowed_ai_modes": [AI_MODE_CODECOMPASS_ONLY, AI_MODE_RESTRICTED_TRANSFORMER],
            "blocked_ai_modes": [AI_MODE_FULL_LLM, AI_MODE_DIRECT_LLM],
            "allowed_model_engines": ["mock"],
        })
    ])
    resolved = policy.resolve("agent/services/auth.py")
    assert not resolved.is_mode_allowed(AI_MODE_FULL_LLM)
    assert not resolved.is_mode_allowed(AI_MODE_DIRECT_LLM)
    assert resolved.is_mode_allowed(AI_MODE_CODECOMPASS_ONLY)
    assert resolved.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER)

    inference = RestrictedModelInferenceService(
        adapters=[MockInferenceAdapter()],
        policy_service=policy,
        use_mock_fallback=False,
    )
    orc = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/services/auth.py", "record_id": "auth", "excerpt": "token validation", "embedding_score": 0.2},
            {"path": "agent/services/log.py", "record_id": "log", "excerpt": "logging", "embedding_score": 0.9},
        ],
        restricted_inference_service=inference,
        path_policy_service=policy,
    )

    result = orc.orchestrate(
        task_text="token validation",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "restricted_inference_rerank_enabled": True,
                "trace_scores": True,
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
    payload = result.context_package.to_dict()
    assert payload["candidates"][0]["path"] == "agent/services/auth.py"
    assert payload["candidates"][0]["score_trace"]["engine"] == "mock"
    assert any(event["event"] == "model_inference_finished" for event in inference.audit_log())
