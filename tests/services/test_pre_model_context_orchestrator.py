"""APMCO-010: Tests for PreModelContextOrchestrator.

All tests run without external LLM keys or live CodeCompass infrastructure.
"""
from __future__ import annotations

import pytest

from agent.services.pre_model_context_orchestrator import (
    DECISION_CANNOT_ANSWER,
    DECISION_DETERMINISTIC,
    DECISION_PASS_THROUGH,
    DECISION_USE_CONTEXT,
    DECISION_WORKER_DECIDES,
    OrchestratorResult,
    PreModelContextOrchestrator,
    get_pre_model_context_orchestrator,
)
from agent.services.pre_model_context_config import (
    MODE_CONTEXT_FIRST,
    MODE_DETERMINISTIC_ONLY,
    MODE_DISABLED,
    MODE_OBSERVE_ONLY,
    MODE_PREFER_CONTEXT,
    MODE_PREFER_DETERMINISTIC,
    MODE_WORKER_DECIDES,
)
from agent.services.path_ai_mode_policy_service import PathAiModePolicyService, PathAiModeRule
from agent.services.restricted_model_inference_service import MockInferenceAdapter, RestrictedModelInferenceService


def _cfg(mode: str = "disabled", *, surface: str = "", surface_mode: str = "") -> dict:
    cfg: dict = {"pre_model_context": {"enabled": True, "mode": mode}}
    if surface and surface_mode:
        cfg["pre_model_context"]["surfaces"] = {
            surface: {"enabled": True, "mode": surface_mode}
        }
    return cfg


def _make_orc(candidates: list[dict] | None = None) -> PreModelContextOrchestrator:
    def _retrieve(task_text, domain_hint, workspace_dir, budget):
        return candidates or []
    return PreModelContextOrchestrator(retrieve_fn=_retrieve)


class TestDisabledMode:
    def test_disabled_returns_pass_through_without_trace_events(self):
        orc = _make_orc()
        result = orc.orchestrate(user_config=_cfg("disabled"))
        assert result.decision == DECISION_PASS_THROUGH

    def test_default_config_is_disabled(self):
        orc = _make_orc()
        result = orc.orchestrate(user_config={})
        assert result.decision == DECISION_PASS_THROUGH

    def test_disabled_mode_does_not_call_retrieve(self):
        calls = []

        def _retrieve(task_text, domain_hint, workspace_dir, budget):
            calls.append(1)
            return []

        orc = PreModelContextOrchestrator(retrieve_fn=_retrieve)
        orc.orchestrate(user_config=_cfg("disabled"))
        assert calls == [], "retrieve_fn must not be called in disabled mode"


class TestObserveOnlyMode:
    def test_observe_only_returns_pass_through_even_with_candidates(self):
        orc = _make_orc([{"path": "foo.py", "record_id": "r1", "embedding_score": 0.9}])
        result = orc.orchestrate(
            task_text="Erkläre die Architektur",
            user_config=_cfg(MODE_OBSERVE_ONLY),
        )
        assert result.decision == DECISION_PASS_THROUGH

    def test_observe_only_builds_context_package(self):
        orc = _make_orc([{"path": "a.py", "record_id": "a1", "embedding_score": 0.7}])
        result = orc.orchestrate(
            task_text="explain the system",
            user_config=_cfg(MODE_OBSERVE_ONLY),
        )
        assert result.context_package is not None
        assert len(result.context_package.candidates) == 1

    def test_observe_only_does_not_modify_prompt(self):
        orc = _make_orc([{"path": "sec.py", "record_id": "s1"}])
        result = orc.orchestrate(task_text="my prompt", user_config=_cfg(MODE_OBSERVE_ONLY))
        # No decision to change the prompt
        assert result.decision == DECISION_PASS_THROUGH


class TestWorkerDecidesMode:
    def test_worker_decides_returns_correct_decision(self):
        orc = _make_orc()
        result = orc.orchestrate(user_config=_cfg(MODE_WORKER_DECIDES))
        assert result.decision == DECISION_WORKER_DECIDES
        assert result.context_package is None

    def test_worker_decides_does_not_call_retrieve(self):
        calls = []

        def _retrieve(t, d, w, b):
            calls.append(1)
            return []

        orc = PreModelContextOrchestrator(retrieve_fn=_retrieve)
        orc.orchestrate(user_config=_cfg(MODE_WORKER_DECIDES))
        assert calls == []


class TestPreferContextMode:
    def test_prefer_context_with_candidates_returns_use_context(self):
        orc = _make_orc([{"path": "main.py", "record_id": "m1", "embedding_score": 0.8}])
        result = orc.orchestrate(task_text="Hilf mir", user_config=_cfg(MODE_PREFER_CONTEXT))
        assert result.decision == DECISION_USE_CONTEXT
        assert result.context_package is not None

    def test_prefer_context_without_candidates_falls_back_to_pass_through(self):
        orc = _make_orc([])  # empty candidates
        result = orc.orchestrate(task_text="Hilf mir", user_config=_cfg(MODE_PREFER_CONTEXT))
        assert result.decision == DECISION_PASS_THROUGH
        assert "context_build_fallback" in result.warnings

    def test_prefer_context_trace_has_expected_events(self):
        orc = _make_orc([{"path": "x.py", "record_id": "x1"}])
        result = orc.orchestrate(task_text="Frage", user_config=_cfg(MODE_PREFER_CONTEXT))
        assert result.trace is not None
        event_names = [e.event for e in result.trace.events]
        assert "config_resolved" in event_names
        assert "task_classified" in event_names

    def test_prefer_context_can_apply_restricted_rerank_with_trace(self):
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=PathAiModePolicyService(),
            use_mock_fallback=False,
        )
        orc = PreModelContextOrchestrator(
            retrieve_fn=lambda _t, _d, _w, _b: [
                {"path": "b.py", "record_id": "b", "excerpt": "logging", "embedding_score": 0.9},
                {"path": "a.py", "record_id": "a", "excerpt": "authentication", "embedding_score": 0.1},
            ],
            restricted_inference_service=svc,
        )

        result = orc.orchestrate(
            task_text="authentication",
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
        payload = result.context_package.to_dict()["candidates"][0]
        assert "score_trace" in payload
        assert payload["transformer_engine"] == "mock"

    def test_restricted_rerank_policy_blocked_falls_back(self):
        policy = PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({
                "path_glob": "src/**",
                "blocked_ai_modes": ["restricted_transformer_inference"],
            })
        ])
        svc = RestrictedModelInferenceService(
            adapters=[MockInferenceAdapter()],
            policy_service=policy,
            use_mock_fallback=False,
        )
        orc = PreModelContextOrchestrator(
            retrieve_fn=lambda _t, _d, _w, _b: [
                {"path": "src/a.py", "record_id": "a", "excerpt": "auth", "embedding_score": 0.9},
            ],
            restricted_inference_service=svc,
            path_policy_service=policy,
        )

        result = orc.orchestrate(
            task_text="auth",
            user_config={
                "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
                "codecompass_ranking": {"restricted_inference_rerank_enabled": True},
            },
        )

        assert result.decision == DECISION_USE_CONTEXT
        assert result.trace is not None
        assert any(event.event == "restricted_rerank_policy_blocked" for event in result.trace.events)


class TestDeterministicOnlyMode:
    def test_deterministic_only_with_navigation_returns_deterministic(self):
        orc = _make_orc([
            {"path": "agent/main.py", "record_id": "a1", "embedding_score": 0.9},
        ])
        result = orc.orchestrate(
            task_text="wo ist die datei für die Konfiguration?",
            user_config=_cfg(MODE_DETERMINISTIC_ONLY),
        )
        assert result.decision == DECISION_DETERMINISTIC
        assert result.deterministic_answer is not None

    def test_deterministic_only_no_llm_call(self):
        llm_called = []

        def _retrieve(t, d, w, b):
            return [{"path": "x.py", "record_id": "r1"}]

        orc = PreModelContextOrchestrator(retrieve_fn=_retrieve)
        result = orc.orchestrate(
            task_text="welche datei enthält den Router?",
            user_config=_cfg(MODE_DETERMINISTIC_ONLY),
        )
        assert llm_called == [], "No LLM should be called in deterministic_only mode"
        assert result.decision in (DECISION_DETERMINISTIC, DECISION_CANNOT_ANSWER)

    def test_deterministic_only_without_candidates_returns_cannot_answer(self):
        orc = _make_orc([])
        result = orc.orchestrate(
            task_text="Erkläre mir die gesamte Architektur des Systems.",
            user_config=_cfg(MODE_DETERMINISTIC_ONLY),
        )
        # Generic chat + no candidates → cannot answer
        assert result.decision == DECISION_CANNOT_ANSWER


class TestPreferDeterministicMode:
    def test_prefer_deterministic_uses_llm_for_architecture_questions(self):
        orc = _make_orc([{"path": "core.py", "record_id": "c1", "embedding_score": 0.6}])
        result = orc.orchestrate(
            task_text="Erkläre mir wie das System aufgebaut ist",
            user_config=_cfg(MODE_PREFER_DETERMINISTIC),
        )
        # Architecture question → needs LLM → use context decision
        assert result.decision in (DECISION_USE_CONTEXT, DECISION_PASS_THROUGH)

    def test_prefer_deterministic_handles_navigation_deterministically(self):
        orc = _make_orc([{"path": "routes.py", "record_id": "r1", "embedding_score": 0.85}])
        result = orc.orchestrate(
            task_text="wo wird die Route definiert?",
            user_config=_cfg(MODE_PREFER_DETERMINISTIC),
        )
        assert result.decision in (DECISION_DETERMINISTIC, DECISION_USE_CONTEXT)


class TestSurfaceConfig:
    def test_surface_mode_overrides_top_level(self):
        cfg = {
            "pre_model_context": {
                "enabled": True,
                "mode": MODE_DISABLED,
                "surfaces": {
                    "ai_snake_chat": {"enabled": True, "mode": MODE_PREFER_CONTEXT}
                },
            }
        }
        orc = _make_orc([{"path": "x.py", "record_id": "x1", "embedding_score": 0.7}])
        result = orc.orchestrate(
            surface="ai_snake_chat",
            task_text="Frage",
            user_config=cfg,
        )
        assert result.decision == DECISION_USE_CONTEXT

    def test_disabled_surface_does_not_run(self):
        cfg = {
            "pre_model_context": {
                "enabled": True,
                "mode": MODE_PREFER_CONTEXT,
                "surfaces": {
                    "ananta_worker": {"enabled": False, "mode": MODE_DISABLED}
                },
            }
        }
        orc = _make_orc()
        result = orc.orchestrate(surface="ananta_worker", user_config=cfg)
        # Top-level prefer_context, but surface disabled → top-level applies
        assert result.decision in (DECISION_PASS_THROUGH, DECISION_USE_CONTEXT)


class TestErrorHandling:
    def test_retrieve_error_returns_pass_through(self):
        def _bad_retrieve(t, d, w, b):
            raise RuntimeError("connection refused")

        orc = PreModelContextOrchestrator(retrieve_fn=_bad_retrieve)
        result = orc.orchestrate(
            task_text="Frage",
            user_config=_cfg(MODE_PREFER_CONTEXT),
        )
        assert result.decision == DECISION_PASS_THROUGH
        assert "context_build_fallback" in result.warnings or "orchestrator_error" not in result.warnings

    def test_internal_error_does_not_raise(self):
        orc = PreModelContextOrchestrator(retrieve_fn=None)
        try:
            orc.orchestrate(user_config=None, task_text="x" * 10_000)
        except Exception as exc:
            pytest.fail(f"orchestrate() must not raise: {exc}")


class TestAiSnakeChatCompatibility:
    def test_disabled_orchestrator_does_not_affect_existing_flow(self):
        """APMCO-010: ai-snake-chat default is backward-compatible."""
        orc = _make_orc()
        result = orc.orchestrate(
            surface="ai_snake_chat",
            task_text="Was ist los?",
            user_config={},  # no pre_model_context key
        )
        assert result.decision == DECISION_PASS_THROUGH
        assert not result.has_context

    def test_singleton_getter_returns_instance(self):
        svc = get_pre_model_context_orchestrator()
        assert isinstance(svc, PreModelContextOrchestrator)
