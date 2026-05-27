"""Tests for Round 5: format schemas, declarative evaluator, Python strategy loader, normalizer, bootstrap strategies."""
from __future__ import annotations

import json
import os
import pytest

# ── T03.04: Declarative evaluator ────────────────────────────────────────────

class TestDeclarativeEvaluator:
    def _hdef(self, triggers=None, selection=None, action=None, domain="tui_snake"):
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        runtime = {"mode": "declarative_rules"}
        if triggers is not None:
            runtime["triggers"] = triggers
        if selection is not None:
            runtime["selection"] = selection
        if action is not None:
            runtime["action"] = action
        return HeuristicDefinition(
            heuristic_id="test-h",
            version="1.0.0",
            domain=domain,
            strategy_kind="follow",
            description="test",
            deterministic=True,
            safety_class="bounded",
            capabilities=(),
            inputs=(),
            outputs=(),
            parameters={"runtime": runtime},
            status="active",
        )

    def _ctx(self, **kwargs):
        from agent.services.heuristic_runtime.decision_context import DecisionContext
        defaults = dict(source_surface="tui_snake", ai_status="offline", recent_events=[])
        defaults.update(kwargs)
        return DecisionContext(**defaults)

    def test_no_triggers_passes_by_default(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(triggers=[], action={"kind": "lurk_near"})
        result, trace = ev.evaluate(hdef, self._ctx())
        assert result.action_kind == "lurk"

    def test_trigger_query_contains_any_matches(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            triggers=[{"query_contains_any": ["klasse", "methode"]}],
            action={"kind": "show_context_summary"},
        )
        result, trace = ev.evaluate(hdef, self._ctx(), query="was macht die klasse Foo")
        assert trace.matched_triggers
        assert result.action_kind in ("chat", "no_action")

    def test_trigger_query_no_match_returns_no_good_match(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            triggers=[{"query_contains_any": ["klasse"]}],
            action={"kind": "show_context_summary"},
        )
        result, trace = ev.evaluate(hdef, self._ctx(), query="unrelated query")
        assert result.action_kind == "no_action"
        assert "no_trigger_matched" in trace.reason_codes

    def test_trigger_ai_status_offline_matches(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            triggers=[{"ai_status_is": "offline"}],
            action={"kind": "follow_with_distance"},
        )
        ctx = self._ctx(ai_status="offline")
        result, trace = ev.evaluate(hdef, ctx)
        assert trace.matched_triggers
        assert result.action_kind == "follow"

    def test_trigger_selected_artifact_present_matches(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            triggers=[{"selected_artifact_present": True}],
            action={"kind": "show_context_summary"},
        )
        ctx = self._ctx(selected_artifacts=["artifact-1"])
        result, trace = ev.evaluate(hdef, ctx)
        assert trace.matched_triggers

    def test_action_follow_with_distance(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(action={"kind": "follow_with_distance", "distance": 4})
        result, trace = ev.evaluate(hdef, self._ctx())
        assert result.action_kind == "follow"
        assert result.suggested_motion is not None

    def test_action_lurk_near(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(action={"kind": "lurk_near"})
        result, trace = ev.evaluate(hdef, self._ctx())
        assert result.action_kind == "lurk"

    def test_action_show_context_summary_with_refs(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            selection={"strategy": "selected_artifacts_first", "max_refs": 3},
            action={"kind": "show_context_summary"},
        )
        ctx = self._ctx(selected_artifacts=["ref-a", "ref-b"])
        result, trace = ev.evaluate(hdef, ctx)
        assert result.action_kind == "chat"
        assert "ref-a" in result.selected_context_refs

    def test_selection_no_good_match_returns_no_match(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            selection={"strategy": "no_good_match"},
            action={"kind": "show_context_summary", "fallback": "no_good_match"},
        )
        result, trace = ev.evaluate(hdef, self._ctx())
        assert result.action_kind == "no_action"

    def test_regex_trigger_matches(self):
        from agent.services.heuristic_runtime.declarative_evaluator import DeclarativeHeuristicEvaluator
        ev = DeclarativeHeuristicEvaluator()
        hdef = self._hdef(
            triggers=[{"query_matches_regex_safe": r"\btest\w+error\b"}],
            action={"kind": "show_context_summary"},
        )
        result, trace = ev.evaluate(hdef, self._ctx(), query="testNotFoundError occurred")
        assert trace.matched_triggers


# ── T04.02: Python strategy loader ───────────────────────────────────────────

class TestPythonStrategyLoader:
    def test_unknown_module_blocked(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        loader = PythonStrategyLoader()
        hdef = HeuristicDefinition(
            heuristic_id="test", version="1.0.0", domain="tui_snake",
            strategy_kind="follow", description="", deterministic=True,
            safety_class="bounded", capabilities=(), inputs=(), outputs=(),
            parameters={"runtime": {
                "mode": "python_strategy",
                "python_strategy": {"module": "evil.malware", "class": "EvilClass"},
            }},
        )
        result = loader.load(hdef)
        assert not result.success
        assert "not_allowlisted" in result.reason_code

    def test_allowlisted_module_not_installed_gives_import_error(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        loader = PythonStrategyLoader()
        hdef = HeuristicDefinition(
            heuristic_id="test", version="1.0.0", domain="tui_snake",
            strategy_kind="follow", description="", deterministic=True,
            safety_class="bounded", capabilities=(), inputs=(), outputs=(),
            parameters={"runtime": {
                "mode": "python_strategy",
                "python_strategy": {
                    "module": "agent.heuristics.strategies.snake_tui.follow_distance",
                    "class": "TuiFollowDistanceStrategy",
                },
            }},
        )
        result = loader.load(hdef)
        assert result.success
        assert result.strategy is not None

    def test_non_python_strategy_mode_fails(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        loader = PythonStrategyLoader()
        hdef = HeuristicDefinition(
            heuristic_id="test", version="1.0.0", domain="tui_snake",
            strategy_kind="follow", description="", deterministic=True,
            safety_class="bounded", capabilities=(), inputs=(), outputs=(),
            parameters={"runtime": {"mode": "declarative_rules"}},
        )
        result = loader.load(hdef)
        assert not result.success
        assert result.reason_code == "not_python_strategy_mode"

    def test_is_allowlisted_check(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        loader = PythonStrategyLoader()
        assert loader.is_allowlisted(
            "agent.heuristics.strategies.snake_tui.follow_distance",
            "TuiFollowDistanceStrategy",
        )
        assert not loader.is_allowlisted("evil.module", "EvilClass")

    def test_all_allowlisted_returns_list(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        loader = PythonStrategyLoader()
        entries = loader.all_allowlisted()
        assert len(entries) > 0
        assert all(isinstance(m, str) and isinstance(c, str) for m, c in entries)


# ── T05.01–T05.02: Normalizer ─────────────────────────────────────────────────

class TestHeuristicNormalizer:
    def _raw(self, **kwargs):
        base = {
            "heuristic_id": "test_heuristic",
            "version": "1.0.0",
            "domain": "tui_snake",
            "deterministic": True,
            "safety_class": "bounded",
            "capabilities": ["read_local_context"],
            "runtime": {"mode": "declarative_rules"},
        }
        base.update(kwargs)
        return base

    def test_normalize_produces_content_hash(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize(self._raw())
        assert result.success
        assert len(result.content_hash) == 64  # SHA-256 hex

    def test_normalize_sets_default_status_candidate(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize(self._raw())
        assert result.normalized["status"] == "candidate"

    def test_yaml_source_forces_candidate_even_if_active(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize(self._raw(status="active"), source_format="yaml")
        assert result.normalized["status"] == "candidate"
        assert any("yaml_source_cannot_be_active" in w for w in result.warnings)

    def test_json_source_preserves_active_status(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize(self._raw(status="active"), source_format="json")
        assert result.normalized["status"] == "active"

    def test_capabilities_are_sorted_deduplicated(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize(self._raw(capabilities=["read_artifact_refs", "read_local_context", "read_local_context"]))
        caps = result.normalized["capabilities"]
        assert caps == sorted(set(caps))
        assert len(caps) == len(set(caps))

    def test_same_content_same_hash(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        r1 = n.normalize(self._raw())
        r2 = n.normalize(self._raw())
        assert r1.content_hash == r2.content_hash

    def test_different_content_different_hash(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        r1 = n.normalize(self._raw(description="A"))
        r2 = n.normalize(self._raw(description="B"))
        assert r1.content_hash != r2.content_hash

    def test_missing_heuristic_id_fails(self):
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize({})
        assert not result.success
        assert result.reason_code == "missing_heuristic_id"

    def test_yaml_not_installed_gives_error(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def patched(name, *a, **kw):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", patched)
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        n = HeuristicNormalizer()
        result = n.normalize_from_yaml("heuristic_id: test")
        assert not result.success


# ── Python strategies ─────────────────────────────────────────────────────────

class TestTuiFollowDistanceStrategy:
    def _hdef(self):
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        return HeuristicDefinition(
            heuristic_id="snake_tui_follow_distance_default",
            version="1.0.0", domain="tui_snake",
            strategy_kind="follow", description="",
            deterministic=True, safety_class="ui_motion_only",
            capabilities=(), inputs=(), outputs=(),
            parameters={"distance": 4},
        )

    def _ctx(self, goal_id=None, artifacts=None):
        from agent.services.heuristic_runtime.decision_context import DecisionContext
        return DecisionContext(
            source_surface="tui_snake",
            active_goal_id=goal_id,
            selected_artifacts=artifacts or [],
            ai_status="offline",
            recent_events=[],
        )

    def test_follow_when_goal_present(self):
        from agent.heuristics.strategies.snake_tui.follow_distance import TuiFollowDistanceStrategy
        s = TuiFollowDistanceStrategy()
        result = s.evaluate(self._ctx(goal_id="goal-1"), self._hdef())
        assert result.action_kind == "follow"
        assert result.source == "heuristic"
        assert result.suggested_motion is not None

    def test_lurk_when_no_target(self):
        from agent.heuristics.strategies.snake_tui.follow_distance import TuiFollowDistanceStrategy
        s = TuiFollowDistanceStrategy()
        result = s.evaluate(self._ctx(), self._hdef())
        assert result.action_kind == "lurk"

    def test_follow_when_artifact_selected(self):
        from agent.heuristics.strategies.snake_tui.follow_distance import TuiFollowDistanceStrategy
        s = TuiFollowDistanceStrategy()
        result = s.evaluate(self._ctx(artifacts=["artifact-1"]), self._hdef())
        assert result.action_kind == "follow"

    def test_is_deterministic_same_goal_same_result(self):
        from agent.heuristics.strategies.snake_tui.follow_distance import TuiFollowDistanceStrategy
        s = TuiFollowDistanceStrategy()
        ctx = self._ctx(goal_id="stable-goal")
        hdef = self._hdef()
        r1 = s.evaluate(ctx, hdef)
        r2 = s.evaluate(ctx, hdef)
        assert r1.suggested_motion.dx == r2.suggested_motion.dx
        assert r1.suggested_motion.dy == r2.suggested_motion.dy


class TestTuiLurkFocusStrategy:
    def _hdef(self):
        from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
        return HeuristicDefinition(
            heuristic_id="snake_tui_lurk_focus_default",
            version="1.0.0", domain="tui_snake",
            strategy_kind="lurk", description="",
            deterministic=True, safety_class="ui_motion_only",
            capabilities=(), inputs=(), outputs=(),
            parameters={"idle_threshold_seconds": 3.0},
        )

    def _ctx(self, ai_status="offline", events=None):
        from agent.services.heuristic_runtime.decision_context import DecisionContext
        return DecisionContext(
            source_surface="tui_snake",
            ai_status=ai_status,
            recent_events=events or [],
        )

    def test_lurk_when_offline(self):
        from agent.heuristics.strategies.snake_tui.lurk_focus import TuiLurkFocusStrategy
        s = TuiLurkFocusStrategy()
        result = s.evaluate(self._ctx(ai_status="offline"), self._hdef())
        assert result.action_kind == "lurk"

    def test_lurk_when_timeout(self):
        from agent.heuristics.strategies.snake_tui.lurk_focus import TuiLurkFocusStrategy
        s = TuiLurkFocusStrategy()
        result = s.evaluate(self._ctx(ai_status="timeout"), self._hdef())
        assert result.action_kind == "lurk"

    def test_lurk_when_pointer_idle_event(self):
        from agent.heuristics.strategies.snake_tui.lurk_focus import TuiLurkFocusStrategy
        s = TuiLurkFocusStrategy()
        ctx = self._ctx(ai_status="available", events=[{"event_type": "pointer_idle"}])
        result = s.evaluate(ctx, self._hdef())
        assert result.action_kind == "lurk"

    def test_fallback_when_ai_available_and_no_idle(self):
        from agent.heuristics.strategies.snake_tui.lurk_focus import TuiLurkFocusStrategy
        s = TuiLurkFocusStrategy()
        result = s.evaluate(self._ctx(ai_status="available"), self._hdef())
        assert result.action_kind == "follow"  # fallback to follow


# ── Scoring utilities ─────────────────────────────────────────────────────────

class TestScoringUtilities:
    def test_clamp_score(self):
        from agent.heuristics.strategies.scoring import clamp_score
        assert clamp_score(1.5) == 1.0
        assert clamp_score(-0.5) == 0.0
        assert clamp_score(0.7) == pytest.approx(0.7)

    def test_weighted_rank_sorts_descending(self):
        from agent.heuristics.strategies.scoring import weighted_rank
        result = weighted_rank(["a", "b", "c"], {"a": 0.3, "b": 0.9, "c": 0.5})
        assert result[0][0] == "b"
        assert result[-1][0] == "a"

    def test_keyword_score(self):
        from agent.heuristics.strategies.scoring import keyword_score
        assert keyword_score("FooError traceback", ["error", "traceback"]) == 1.0
        assert keyword_score("hello world", ["error"]) == 0.0

    def test_is_below_threshold(self):
        from agent.heuristics.strategies.scoring import is_below_threshold
        assert is_below_threshold(0.3, 0.5)
        assert not is_below_threshold(0.7, 0.5)


# ── Bootstrap heuristic JSON files ───────────────────────────────────────────

class TestBootstrapHeuristicFiles:
    BOOTSTRAP_FILES = [
        "heuristics/active/snake_tui_follow_distance_default.heuristic.json",
        "heuristics/active/snake_tui_lurk_focus_default.heuristic.json",
        "heuristics/active/chat_codecompass_selected_artifact_first.heuristic.json",
        "heuristics/active/chat_codecompass_no_good_match_default.heuristic.json",
    ]

    def test_all_bootstrap_files_exist(self):
        for path in self.BOOTSTRAP_FILES:
            assert os.path.exists(path), f"Missing: {path}"

    def test_all_bootstrap_files_are_valid_json(self):
        for path in self.BOOTSTRAP_FILES:
            with open(path) as f:
                data = json.load(f)
            assert "heuristic_id" in data
            assert "domain" in data

    def test_all_bootstrap_are_deterministic(self):
        for path in self.BOOTSTRAP_FILES:
            with open(path) as f:
                data = json.load(f)
            assert data.get("deterministic") is True, f"{path}: deterministic must be true"

    def test_snake_heuristics_have_ui_motion_only_safety(self):
        for path in self.BOOTSTRAP_FILES:
            if "snake" not in path:
                continue
            with open(path) as f:
                data = json.load(f)
            assert data.get("safety_class") in ("ui_motion_only", "readonly"), \
                f"{path}: snake safety_class must be ui_motion_only or readonly"

    def test_no_bootstrap_has_file_write_or_network(self):
        forbidden = {"file_write", "network_access", "secret_access"}
        for path in self.BOOTSTRAP_FILES:
            with open(path) as f:
                data = json.load(f)
            caps = set(data.get("capabilities") or [])
            assert not caps.intersection(forbidden), \
                f"{path}: forbidden capabilities found: {caps & forbidden}"

    def test_index_includes_all_bootstrap(self):
        with open("heuristics/index.json") as f:
            idx = json.load(f)
        indexed_ids = {h["heuristic_id"] for h in idx.get("heuristics", [])}
        for path in self.BOOTSTRAP_FILES:
            with open(path) as f:
                data = json.load(f)
            assert data["heuristic_id"] in indexed_ids, \
                f"{data['heuristic_id']} not in index.json"
