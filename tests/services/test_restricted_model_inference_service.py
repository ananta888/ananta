"""RTIPM-008: Tests for RestrictedModelInferenceService and MockInferenceAdapter.

All tests run without any ML dependency (no torch, no transformers, etc.).
Uses MockInferenceAdapter for deterministic results.
"""
from __future__ import annotations

import pytest

from agent.services.restricted_model_inference_service import (
    MockInferenceAdapter,
    RestrictedModelInferenceService,
    OP_CLASSIFY,
    OP_EMBED,
    OP_RERANK,
    OP_SCORE_CHOICES,
    get_restricted_model_inference_service,
    reset_restricted_model_inference_service,
    validate_no_generation,
)
from agent.services.model_inference_adapters import (
    ChoiceScore,
    ClassificationResult,
    RerankResult,
)
from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    PathAiModeRule,
)


def _svc(policy: PathAiModePolicyService | None = None) -> RestrictedModelInferenceService:
    return RestrictedModelInferenceService(
        adapters=[MockInferenceAdapter()],
        policy_service=policy or PathAiModePolicyService(),
        use_mock_fallback=False,
    )


class TestMockAdapterBasics:
    def test_embed_returns_vectors(self):
        adapter = MockInferenceAdapter(dims=4)
        vecs = adapter.embed(["hello", "world"])
        assert len(vecs) == 2
        assert all(len(v) == 4 for v in vecs)

    def test_embed_same_text_same_vector(self):
        adapter = MockInferenceAdapter(dims=8)
        v1 = adapter.embed(["deterministic"])
        v2 = adapter.embed(["deterministic"])
        assert v1 == v2

    def test_classify_returns_known_label(self):
        adapter = MockInferenceAdapter()
        labels = ["bugfix", "security", "generic"]
        result = adapter.classify("fix the crash", labels)
        assert result.label in labels
        assert 0.0 < result.confidence <= 1.0

    def test_rerank_scores_in_range(self):
        adapter = MockInferenceAdapter()
        candidates = [
            {"path": "a.py", "record_id": "a", "excerpt": "authentication code"},
            {"path": "b.py", "record_id": "b", "excerpt": "logging utility"},
        ]
        results = adapter.rerank("find auth code", candidates)
        assert len(results) == 2
        assert all(0.0 <= r.score <= 1.0 for r in results)

    def test_rerank_is_sorted_by_score_desc(self):
        adapter = MockInferenceAdapter()
        candidates = [{"path": f"f{i}.py", "record_id": str(i)} for i in range(5)]
        results = adapter.rerank("query", candidates)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_score_choices_sums_to_1(self):
        adapter = MockInferenceAdapter()
        results = adapter.score_choices("Is this security-critical?", ["yes", "no", "maybe"])
        total = sum(r.score for r in results)
        assert abs(total - 1.0) < 0.01, f"Scores should sum to ~1.0, got {total}"

    def test_score_choices_no_generation_flag(self):
        adapter = MockInferenceAdapter()
        results = adapter.score_choices("prompt", ["a", "b"])
        assert all(r.no_generation for r in results)

    def test_extract_features_matches_embed(self):
        adapter = MockInferenceAdapter(dims=6)
        fv = adapter.extract_features("some text")
        emb = adapter.embed(["some text"])[0]
        assert fv.vector == emb

    def test_risk_score_category_is_valid(self):
        adapter = MockInferenceAdapter()
        result = adapter.risk_score({"path": "src/auth/login.py", "symbols": "verify_password"})
        assert result.risk_category in ("low", "medium", "high", "critical")
        assert 0.0 <= result.risk_score <= 1.0

    def test_status_is_ready(self):
        adapter = MockInferenceAdapter()
        st = adapter.status()
        assert st.status == "ready"
        assert st.engine == "mock"


class TestRestrictedModelInferenceService:
    def test_embed_returns_vectors(self):
        svc = _svc()
        result = svc.embed(["hello"])
        assert len(result) == 1
        assert isinstance(result[0], list)

    def test_classify_returns_result(self):
        svc = _svc()
        result = svc.classify("fix auth bug", ["security", "bugfix", "other"])
        assert isinstance(result, ClassificationResult)
        assert result.label in ["security", "bugfix", "other"]

    def test_rerank_returns_list_of_results(self):
        svc = _svc()
        candidates = [
            {"path": "a.py", "record_id": "a", "excerpt": "auth token"},
            {"path": "b.py", "record_id": "b", "excerpt": "logging"},
        ]
        results = svc.rerank("authentication", candidates)
        assert all(isinstance(r, RerankResult) for r in results)

    def test_score_choices_returns_choice_scores(self):
        svc = _svc()
        results = svc.score_choices("Is this risky?", ["yes", "no"])
        assert all(isinstance(r, ChoiceScore) for r in results)
        assert all(r.no_generation for r in results)

    def test_risk_score_no_generation(self):
        svc = _svc()
        result = svc.risk_score({"path": "auth.py"})
        assert result.no_generation

    def test_adapter_status_list(self):
        svc = _svc()
        statuses = svc.get_adapter_statuses()
        assert len(statuses) >= 1
        assert all(hasattr(s, "status") for s in statuses)

    def test_audit_log_populated_after_operation(self):
        svc = _svc()
        svc.embed(["test"])
        log = svc.audit_log()
        assert len(log) == 1
        assert log[0]["event"] == "model_inference_finished"
        assert log[0]["operation"] == OP_EMBED

    def test_multiple_ops_build_audit_log(self):
        svc = _svc()
        svc.embed(["a"])
        svc.classify("b", ["x", "y"])
        svc.risk_score({"path": "c.py"})
        assert len(svc.audit_log()) == 3

    def test_mock_fallback_used_when_no_real_adapter(self):
        svc = RestrictedModelInferenceService(
            adapters=[],
            policy_service=PathAiModePolicyService(),
            use_mock_fallback=True,
        )
        result = svc.embed(["text"])
        assert len(result) == 1

    def test_no_fallback_no_adapter_raises(self):
        svc = RestrictedModelInferenceService(
            adapters=[],
            policy_service=PathAiModePolicyService(),
            use_mock_fallback=False,
        )
        with pytest.raises(RestrictedModelInferenceService.NoDegradedFallbackError):
            svc.embed(["text"])


class TestPolicyGating:
    def _blocked_policy(self) -> PathAiModePolicyService:
        return PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({
                "path_glob": "src/security/**",
                "blocked_ai_modes": [AI_MODE_RESTRICTED_TRANSFORMER],
            })
        ])

    def test_blocked_path_raises_inference_blocked_error(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=self._blocked_policy(),
            use_mock_fallback=False,
        )
        with pytest.raises(RestrictedModelInferenceService.InferenceBlockedError):
            svc.embed(["text"], path="src/security/auth.py")

    def test_blocked_path_adds_audit_event(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=self._blocked_policy(),
            use_mock_fallback=False,
        )
        try:
            svc.rerank("q", [], path="src/security/auth.py")
        except RestrictedModelInferenceService.InferenceBlockedError:
            pass
        audit = svc.audit_log()
        assert any(e["event"] == "model_inference_blocked" for e in audit)
        blocked = next(e for e in audit if e["event"] == "model_inference_blocked")
        assert blocked["reason_code"] == "policy_blocked_restricted_transformer"

    def test_non_blocked_path_succeeds(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=self._blocked_policy(),
            use_mock_fallback=False,
        )
        result = svc.embed(["text"], path="docs/readme.md")
        assert len(result) == 1

    def test_no_path_skips_policy_check(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=self._blocked_policy(),
            use_mock_fallback=False,
        )
        result = svc.embed(["text"])  # no path arg
        assert len(result) == 1


class TestValidateNoGeneration:
    def test_valid_choices_pass(self):
        choices = [ChoiceScore(choice="a", score=0.7, no_generation=True)]
        validate_no_generation(choices)  # should not raise

    def test_generation_flag_false_raises(self):
        choices = [ChoiceScore(choice="generated text output", score=0.9, no_generation=False)]
        with pytest.raises(ValueError, match="no_generation=False"):
            validate_no_generation(choices)


class TestSingleton:
    def teardown_method(self):
        reset_restricted_model_inference_service(None)

    def test_getter_returns_instance(self):
        svc = get_restricted_model_inference_service()
        assert isinstance(svc, RestrictedModelInferenceService)

    def test_reset_replaces_singleton(self):
        new = RestrictedModelInferenceService(use_mock_fallback=True)
        reset_restricted_model_inference_service(new)
        assert get_restricted_model_inference_service() is new
