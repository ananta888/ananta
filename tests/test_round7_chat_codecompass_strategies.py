"""Round 7 tests: chat_codecompass Python strategies and their bootstrap JSON files."""
from __future__ import annotations

import json
import os

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


def _def(heuristic_id: str = "test", parameters: dict | None = None) -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id=heuristic_id,
        version="1.0.0",
        domain="chat_codecompass",
        strategy_kind="show_context_summary",
        description="test",
        deterministic=True,
        safety_class="readonly",
        capabilities=(),
        inputs=(),
        outputs=(),
        parameters=parameters or {},
    )


def _ctx(
    query: str = "",
    scopes: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> DecisionContext:
    return DecisionContext(
        source_surface="chat_codecompass",
        ai_status="available",
        query=query or None,
        allowed_source_scopes=scopes or [],
        selected_artifacts=artifacts or [],
    )


# ── SelectedArtifactFirstStrategy ────────────────────────────────────────────

class TestSelectedArtifactFirstStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.selected_artifact_first import (
            SelectedArtifactFirstStrategy,
        )
        return SelectedArtifactFirstStrategy()

    def test_no_action_when_no_artifact(self):
        r = self._s().evaluate(_ctx(), _def())
        assert r.action_kind == "no_action"

    def test_open_source_ref_when_artifact_selected(self):
        r = self._s().evaluate(_ctx(artifacts=["SomeClass.java"]), _def())
        assert r.action_kind == "open_source_ref"
        assert r.confidence == 0.95

    def test_reason_code_contains_artifact(self):
        r = self._s().evaluate(_ctx(artifacts=["Foo.java"]), _def())
        assert any("selected_artifact:" in rc for rc in r.reason_codes)

    def test_reason_code_reports_scope_count(self):
        r = self._s().evaluate(
            _ctx(artifacts=["X.py"], scopes=["scope_a", "scope_b"]), _def()
        )
        assert any("scope_count:2" in rc for rc in r.reason_codes)


# ── SymbolLookupStrategy ──────────────────────────────────────────────────────

class TestSymbolLookupStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.symbol_lookup import SymbolLookupStrategy
        return SymbolLookupStrategy()

    def test_no_action_on_empty_query(self):
        r = self._s().evaluate(_ctx(query=""), _def())
        assert r.action_kind == "no_action"

    def test_no_action_when_no_scopes(self):
        # query has enough symbol keywords to pass score threshold but no scopes
        r = self._s().evaluate(
            _ctx(query="class method function interface implements extends"), _def()
        )
        assert r.action_kind == "no_action"
        assert any("no_scopes" in rc for rc in r.reason_codes)

    def test_open_source_ref_with_symbol_query_and_scope(self):
        r = self._s().evaluate(
            _ctx(query="class Foo extends Bar implements Baz interface method", scopes=["src_main"]),
            _def(),
        )
        assert r.action_kind == "open_source_ref"

    def test_confidence_scales_with_score(self):
        # Both queries exceed threshold; higher keyword density → higher confidence
        r_low = self._s().evaluate(
            _ctx(query="class method import", scopes=["s"]), _def()
        )
        r_high = self._s().evaluate(
            _ctx(query="class method function interface implements extends import override enum def", scopes=["s"]),
            _def(),
        )
        # Both should produce open_source_ref; r_high has more matched keywords
        assert r_low.action_kind == "open_source_ref"
        assert r_high.action_kind == "open_source_ref"
        assert r_high.confidence >= r_low.confidence

    def test_min_score_param_respected(self):
        # Very high min_score forces no_action
        r = self._s().evaluate(
            _ctx(query="class Foo", scopes=["s"]),
            _def(parameters={"min_score": 0.99}),
        )
        assert r.action_kind == "no_action"


# ── ErrorLookupStrategy ───────────────────────────────────────────────────────

class TestErrorLookupStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.error_lookup import ErrorLookupStrategy
        return ErrorLookupStrategy()

    def test_no_action_on_non_error_query(self):
        r = self._s().evaluate(_ctx(query="what is the design pattern"), _def())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_error_but_no_refs(self):
        # need enough error keywords to pass threshold (min_score=0.15)
        r = self._s().evaluate(_ctx(query="NullPointerException error crash exception"), _def())
        assert r.action_kind == "ask_scope"

    def test_show_context_summary_with_error_and_scope(self):
        r = self._s().evaluate(
            _ctx(query="getting a NullPointerException error in production", scopes=["logs"]),
            _def(),
        )
        assert r.action_kind == "show_context_summary"

    def test_show_context_summary_with_error_and_artifact(self):
        r = self._s().evaluate(
            _ctx(query="crash and fail exception stacktrace", artifacts=["ErrorClass.java"]),
            _def(),
        )
        assert r.action_kind == "show_context_summary"


# ── TodoStatusStrategy ────────────────────────────────────────────────────────

class TestTodoStatusStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.todo_status import TodoStatusStrategy
        return TodoStatusStrategy()

    def test_no_action_on_unrelated_query(self):
        r = self._s().evaluate(_ctx(query="how does the auth flow work"), _def())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_task_query_no_scopes(self):
        r = self._s().evaluate(_ctx(query="what tasks are in progress"), _def())
        assert r.action_kind == "ask_scope"

    def test_show_context_summary_with_task_scope(self):
        r = self._s().evaluate(
            _ctx(
                query="what tasks are blocked",
                scopes=["todo_main", "task_tracker"],
            ),
            _def(),
        )
        assert r.action_kind == "show_context_summary"

    def test_reason_codes_contain_scope_count(self):
        r = self._s().evaluate(
            _ctx(query="next task status", scopes=["todo_a", "task_b"]),
            _def(),
        )
        assert any("scopes:2" in rc for rc in r.reason_codes)


# ── SourcePackLookupStrategy ──────────────────────────────────────────────────

class TestSourcePackLookupStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.sourcepack_lookup import (
            SourcePackLookupStrategy,
        )
        return SourcePackLookupStrategy()

    def test_no_action_without_scopes(self):
        r = self._s().evaluate(_ctx(query="tell me about the api"), _def())
        assert r.action_kind == "no_action"

    def test_open_source_ref_with_scopes(self):
        r = self._s().evaluate(
            _ctx(query="what does the service do", scopes=["service_pack", "api_pack"]),
            _def(),
        )
        assert r.action_kind == "open_source_ref"

    def test_reason_includes_top_scope(self):
        r = self._s().evaluate(
            _ctx(query="api route handler", scopes=["api_service"]),
            _def(),
        )
        assert any("sourcepack_lookup:scope=" in rc for rc in r.reason_codes)

    def test_confidence_is_stable(self):
        r = self._s().evaluate(
            _ctx(query="model schema type", scopes=["models"]),
            _def(),
        )
        assert r.confidence == 0.75


# ── NoGoodMatchStrategy ───────────────────────────────────────────────────────

class TestNoGoodMatchStrategy:
    def _s(self):
        from agent.heuristics.strategies.chat_codecompass.no_good_match import NoGoodMatchStrategy
        return NoGoodMatchStrategy()

    def test_always_no_action(self):
        r = self._s().evaluate(_ctx(query="anything at all"), _def())
        assert r.action_kind == "no_action"

    def test_confidence_is_one(self):
        r = self._s().evaluate(_ctx(), _def())
        assert r.confidence == 1.0

    def test_reason_code_is_anti_hallucination(self):
        r = self._s().evaluate(_ctx(), _def())
        assert any("anti_hallucination" in rc for rc in r.reason_codes)


# ── Bootstrap JSON files ──────────────────────────────────────────────────────

_HEURISTICS_DIR = os.path.join(os.path.dirname(__file__), "..", "heuristics", "active")

_CHAT_NEW_FILES = [
    "chat_codecompass_symbol_lookup_default.heuristic.json",
    "chat_codecompass_error_lookup_default.heuristic.json",
    "chat_codecompass_todo_status_default.heuristic.json",
    "chat_codecompass_sourcepack_lookup_default.heuristic.json",
]


class TestRound7BootstrapFiles:
    def _load(self, fn: str) -> dict:
        return json.load(open(os.path.join(_HEURISTICS_DIR, fn)))

    def test_all_new_files_exist(self):
        for fn in _CHAT_NEW_FILES:
            assert os.path.exists(os.path.join(_HEURISTICS_DIR, fn)), f"Missing: {fn}"

    def test_all_are_deterministic(self):
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            assert d.get("deterministic") is True, f"{fn}: must be deterministic"

    def test_all_have_readonly_safety(self):
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            assert d.get("safety_class") == "readonly", f"{fn}: expected readonly"

    def test_all_use_python_strategy_mode(self):
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            mode = d.get("runtime", {}).get("mode")
            assert mode == "python_strategy", f"{fn}: expected python_strategy, got {mode}"

    def test_chat_ttl_in_correct_range(self):
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            ttl = d.get("ttl_policy", {})
            assert ttl.get("min_seconds", 0) >= 10.0, f"{fn}: min_seconds must be >= 10"
            assert ttl.get("max_seconds", 0) <= 20.0, f"{fn}: max_seconds must be <= 20"

    def test_no_forbidden_capabilities(self):
        forbidden = {"file_write", "network_access", "secret_access"}
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            bad = set(d.get("capabilities", [])) & forbidden
            assert not bad, f"{fn}: forbidden caps: {bad}"

    def test_python_strategy_modules_allowlisted(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        loader = PythonStrategyLoader()
        for fn in _CHAT_NEW_FILES:
            d = self._load(fn)
            mod = d["runtime"]["python_strategy"]["module"]
            cls = d["runtime"]["python_strategy"]["class"]
            assert loader.is_allowlisted(mod, cls), f"{fn}: not allowlisted: {mod}.{cls}"

    def test_index_includes_all_new_entries(self):
        index = json.load(
            open(os.path.join(os.path.dirname(__file__), "..", "heuristics", "index.json"))
        )
        ids = {h["heuristic_id"] for h in index["heuristics"]}
        for fn in _CHAT_NEW_FILES:
            hid = fn.replace(".heuristic.json", "")
            assert hid in ids, f"index.json missing: {hid}"
