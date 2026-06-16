"""RTIPM-006 / RTIPM-008: Tests that prove hard separation between
restricted transformer inference and free text generation.

Ensures:
- No operation on RestrictedModelInferenceService or any adapter produces
  free text (no_generation=True on all result types).
- full_llm blocked for src/security/** while restricted_transformer_inference
  remains allowed.
- score_choices only returns scores for provided choices, not generated text.
- Blocked attempts appear in audit log with reason_code.
"""
from __future__ import annotations

import pytest

from agent.services.restricted_model_inference_service import (
    MockInferenceAdapter,
    RestrictedModelInferenceService,
    validate_no_generation,
)
from agent.services.model_inference_adapters import (
    ChoiceScore,
    ClassificationResult,
    RerankResult,
    RiskScoreResult,
    FeatureVector,
)
from agent.services.path_ai_mode_policy_service import (
    AI_MODE_CODECOMPASS_ONLY,
    AI_MODE_DETERMINISTIC_ONLY,
    AI_MODE_EMBEDDING_ONLY,
    AI_MODE_FULL_LLM,
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    PathAiModeRule,
)


def _svc_with_security_policy() -> RestrictedModelInferenceService:
    """Security path: full_llm blocked, restricted_transformer_inference allowed."""
    policy = PathAiModePolicyService(rules=[
        PathAiModeRule.from_raw({
            "path_glob": "src/security/**",
            "allowed_ai_modes": [
                AI_MODE_CODECOMPASS_ONLY,
                AI_MODE_EMBEDDING_ONLY,
                AI_MODE_RESTRICTED_TRANSFORMER,
                AI_MODE_DETERMINISTIC_ONLY,
            ],
            "blocked_ai_modes": [AI_MODE_FULL_LLM],
            "allow_free_text_generation": False,
            "allow_tool_decision_from_model_text": False,
        })
    ])
    return RestrictedModelInferenceService(
        adapters=[MockInferenceAdapter()],
        policy_service=policy,
        use_mock_fallback=False,
    )


class TestNoGenerationContract:
    """All result types must have no_generation=True."""

    def test_classification_result_has_no_generation(self):
        adapter = MockInferenceAdapter()
        result = adapter.classify("fix auth bug", ["security", "bugfix"])
        assert isinstance(result, ClassificationResult)
        assert result.no_generation is True

    def test_rerank_result_has_no_generation(self):
        adapter = MockInferenceAdapter()
        results = adapter.rerank("query", [{"path": "x.py", "record_id": "x"}])
        assert all(r.no_generation for r in results)

    def test_choice_score_has_no_generation(self):
        adapter = MockInferenceAdapter()
        results = adapter.score_choices("Is this risky?", ["yes", "no"])
        assert all(r.no_generation for r in results)

    def test_feature_vector_has_no_generation(self):
        adapter = MockInferenceAdapter()
        result = adapter.extract_features("some code")
        assert result.no_generation is True

    def test_risk_score_has_no_generation(self):
        adapter = MockInferenceAdapter()
        result = adapter.risk_score({"path": "auth.py"})
        assert result.no_generation is True

    def test_validate_no_generation_catches_violation(self):
        bad = [ChoiceScore(choice="generated answer", score=0.9, no_generation=False)]
        with pytest.raises(ValueError):
            validate_no_generation(bad)

    def test_validate_no_generation_passes_for_valid(self):
        good = [
            ChoiceScore(choice="option_a", score=0.6, no_generation=True),
            ChoiceScore(choice="option_b", score=0.4, no_generation=True),
        ]
        validate_no_generation(good)  # must not raise


class TestGenerativeCallNeverInvoked:
    """Prove restricted inference does not invoke generative model paths."""

    def test_score_choices_returns_only_provided_choices(self):
        svc = _svc_with_security_policy()
        fixed_choices = ["yes", "no", "unknown"]
        results = svc.score_choices("Is this a security issue?", fixed_choices)
        returned_choices = {r.choice for r in results}
        assert returned_choices == set(fixed_choices), (
            f"score_choices must only return scores for provided choices, "
            f"got extra: {returned_choices - set(fixed_choices)}"
        )

    def test_embed_returns_vectors_not_text(self):
        svc = _svc_with_security_policy()
        results = svc.embed(["some code snippet"])
        assert isinstance(results[0], list)
        assert all(isinstance(x, float) for x in results[0])

    def test_classify_returns_fixed_label(self):
        svc = _svc_with_security_policy()
        labels = ["security", "bugfix", "other"]
        result = svc.classify("auth token expired", labels)
        assert result.label in labels, (
            f"classify must return one of {labels}, got {result.label!r}"
        )

    def test_rerank_score_is_not_text(self):
        svc = _svc_with_security_policy()
        candidates = [{"path": "src/auth.py", "record_id": "a"}]
        results = svc.rerank("find auth code", candidates)
        for r in results:
            assert isinstance(r.score, float), f"Expected float score, got {type(r.score)}"


class TestSecurityPathPolicy:
    """Security path: full_llm blocked, restricted_transformer_inference allowed."""

    def test_restricted_inference_allowed_on_security_path(self):
        svc = _svc_with_security_policy()
        # Should NOT raise
        result = svc.embed(["code"], path="src/security/auth.py")
        assert len(result) == 1

    def test_full_llm_blocked_shows_in_policy_check(self):
        from agent.services.path_ai_mode_policy_service import PathAiModePolicyService
        policy = PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({
                "path_glob": "src/security/**",
                "blocked_ai_modes": [AI_MODE_FULL_LLM],
            })
        ])
        result = policy.resolve("src/security/auth.py")
        assert not result.is_mode_allowed(AI_MODE_FULL_LLM)
        assert result.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER)

    def test_blocked_restricted_inference_raises_and_audits(self):
        policy = PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({
                "path_glob": "src/secrets/**",
                "blocked_ai_modes": [AI_MODE_RESTRICTED_TRANSFORMER],
            })
        ])
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=policy,
            use_mock_fallback=False,
        )
        with pytest.raises(RestrictedModelInferenceService.InferenceBlockedError):
            svc.embed(["text"], path="src/secrets/api_keys.py")

        audit = svc.audit_log()
        blocked_events = [e for e in audit if e["event"] == "model_inference_blocked"]
        assert len(blocked_events) == 1
        assert blocked_events[0]["reason_code"] == "policy_blocked_restricted_transformer"

    def test_docs_path_allows_all_modes(self):
        policy = PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({
                "path_glob": "src/security/**",
                "blocked_ai_modes": [AI_MODE_FULL_LLM],
            })
        ])
        result = policy.resolve("docs/guide.md")
        assert result.is_mode_allowed(AI_MODE_FULL_LLM)
        assert result.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER)


class TestBackwardCompatibility:
    """Prove that no existing behavior changes without explicit config."""

    def test_no_policy_never_blocks_any_mode(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=PathAiModePolicyService(),
            use_mock_fallback=False,
        )
        # All of these should succeed without raising
        svc.embed(["text"], path="src/main.py")
        svc.classify("text", ["a", "b"], path="src/security/auth.py")
        svc.rerank("q", [], path="any/path.py")

    def test_degraded_fallback_still_no_generation(self):
        svc = RestrictedModelInferenceService(use_mock_fallback=True)
        results = svc.score_choices("Is this right?", ["yes", "no"])
        assert all(r.no_generation for r in results)
