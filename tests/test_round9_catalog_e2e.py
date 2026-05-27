"""Round 9 tests: catalog validation, no-hallucination policy, E2E evaluations,
integration tests with Registry/Loader, and fallback chains."""
from __future__ import annotations

import json
import os

import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_ACTIVE_DIR = os.path.join(_ROOT, "heuristics", "active")
_INDEX_PATH = os.path.join(_ROOT, "heuristics", "index.json")
_BINDINGS_PATH = os.path.join(_ROOT, "heuristics", "python_strategy_bindings.json")
_CHAINS_PATH = os.path.join(_ROOT, "heuristics", "fallback_chains.json")


def _all_bootstrap_files() -> list[str]:
    return sorted(
        os.path.join(_ACTIVE_DIR, fn)
        for fn in os.listdir(_ACTIVE_DIR)
        if fn.endswith(".heuristic.json")
    )


def _load(path: str) -> dict:
    return json.load(open(path))


def _make_def(
    heuristic_id: str = "test",
    domain: str = "chat_codecompass",
    parameters: dict | None = None,
) -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id=heuristic_id,
        version="1.0.0",
        domain=domain,
        strategy_kind="test",
        description="test",
        deterministic=True,
        safety_class="readonly",
        capabilities=(),
        inputs=(),
        outputs=(),
        parameters=parameters or {},
    )


def _load_strategy(heuristic_id: str):
    """Load and instantiate the Python strategy from a python_strategy-mode bootstrap file."""
    from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
    path = os.path.join(_ACTIVE_DIR, f"{heuristic_id}.heuristic.json")
    data = _load(path)
    py = data["runtime"]["python_strategy"]
    lr = PythonStrategyLoader().load_module_class(py["module"], py["class"])
    assert lr.success, f"{heuristic_id}: {lr.reason_code}"
    defn = HeuristicDefinition.from_dict(data)
    return lr.strategy, defn


def _ctx(surface: str = "tui_snake", **kwargs) -> DecisionContext:
    defaults = dict(source_surface=surface, ai_status="offline")
    defaults.update(kwargs)
    return DecisionContext(**defaults)


# ── catalog T07.01: Bootstrap catalog validation ──────────────────────────────

class TestHeuristicCatalogValidator:
    def _validator(self):
        from agent.services.heuristic_runtime.heuristic_catalog_validator import (
            HeuristicCatalogValidator,
        )
        return HeuristicCatalogValidator()

    def test_all_bootstrap_files_pass_validation(self):
        v = self._validator()
        result = v.validate_directory(_ACTIVE_DIR)
        failed = [r for r in result.results if not r.passed]
        assert result.all_passed, (
            f"{result.failed}/{result.total} failed:\n"
            + "\n".join(f"  {r.file}: {r.errors}" for r in failed)
        )

    def test_validate_directory_counts_all_files(self):
        v = self._validator()
        result = v.validate_directory(_ACTIVE_DIR)
        expected = len([f for f in os.listdir(_ACTIVE_DIR) if f.endswith(".heuristic.json")])
        assert result.total == expected

    def test_invalid_json_gives_failed_result(self, tmp_path):
        v = self._validator()
        bad = tmp_path / "bad.heuristic.json"
        bad.write_text("{not valid json")
        fr = v.validate_file(str(bad))
        assert not fr.passed
        assert any("invalid_json" in e for e in fr.errors)

    def test_missing_required_field_fails(self, tmp_path):
        v = self._validator()
        data = {
            "heuristic_id": "test_missing",
            "version": "1.0.0",
            "domain": "tui_snake",
            "safety_class": "ui_motion_only",
            "capabilities": [],
            "runtime": {"mode": "declarative_rules"},
        }
        f = tmp_path / "missing.heuristic.json"
        f.write_text(json.dumps(data))
        fr = v.validate_file(str(f))
        assert not fr.passed  # missing 'deterministic'

    def test_forbidden_capability_fails(self, tmp_path):
        v = self._validator()
        data = {
            "heuristic_id": "bad_caps",
            "version": "1.0.0",
            "domain": "chat_codecompass",
            "deterministic": True,
            "safety_class": "readonly",
            "capabilities": ["file_write"],
            "runtime": {"mode": "declarative_rules"},
        }
        f = tmp_path / "bad_caps.heuristic.json"
        f.write_text(json.dumps(data))
        fr = v.validate_file(str(f))
        assert not fr.passed
        assert any("forbidden_capabilities" in e for e in fr.errors)

    def test_summary_reports_counts(self):
        v = self._validator()
        result = v.validate_directory(_ACTIVE_DIR)
        assert result.summary().startswith(f"{result.passed}/{result.total}")


# ── catalog T07.04: No-hallucination and policy tests ─────────────────────────

class TestNoHallucinationPolicy:
    def test_all_bootstrap_are_deterministic(self):
        for path in _all_bootstrap_files():
            d = _load(path)
            assert d.get("deterministic") is True, f"{os.path.basename(path)}: must be deterministic"

    def test_no_bootstrap_has_forbidden_capabilities(self):
        forbidden = {"file_write", "network_access", "secret_access"}
        for path in _all_bootstrap_files():
            d = _load(path)
            bad = set(d.get("capabilities", [])) & forbidden
            assert not bad, f"{os.path.basename(path)}: forbidden caps {bad}"

    def test_snake_bootstraps_have_ui_motion_only(self):
        snake_domains = {"tui_snake", "snake_eclipse"}
        for path in _all_bootstrap_files():
            d = _load(path)
            if d.get("domain") in snake_domains:
                sc = d.get("safety_class")
                assert sc == "ui_motion_only", f"{os.path.basename(path)}: got {sc}"

    def test_no_bootstrap_has_status_candidate(self):
        for path in _all_bootstrap_files():
            d = _load(path)
            assert d.get("status") in ("active", None), (
                f"{os.path.basename(path)}: status={d.get('status')}"
            )

    def test_chat_bootstraps_have_readonly_safety(self):
        for path in _all_bootstrap_files():
            d = _load(path)
            if d.get("domain") == "chat_codecompass":
                assert d.get("safety_class") == "readonly", os.path.basename(path)

    def test_no_good_match_strategy_always_returns_no_action(self):
        from agent.heuristics.strategies.chat_codecompass.no_good_match import NoGoodMatchStrategy
        s = NoGoodMatchStrategy()
        for query in ["", "anything", "class method error fail merge"]:
            ctx = DecisionContext(source_surface="chat_codecompass", ai_status="available", query=query or None)
            result = s.evaluate(_make_def(), _make_def())
            assert result.action_kind == "no_action"
            assert result.confidence == 1.0


# ── catalog T08.01: E2E Snake bootstrap without AI ────────────────────────────

class TestE2ESnakeBootstrap:
    def test_artifact_intent_fires_with_artifact(self):
        s, defn = _load_strategy("snake_tui_artifact_intent_default")
        r = s.evaluate(_ctx(selected_artifacts=["Foo.java"]), defn)
        assert r.action_kind == "follow"
        assert r.source == "heuristic"

    def test_diff_focus_fires_in_diff_panel(self):
        s, defn = _load_strategy("snake_tui_diff_focus_default")
        r = s.evaluate(_ctx(active_panel="diff_view"), defn)
        assert r.action_kind == "lurk"

    def test_artifact_intent_lurks_without_artifact(self):
        s, defn = _load_strategy("snake_tui_artifact_intent_default")
        r = s.evaluate(_ctx(selected_artifacts=[]), defn)
        assert r.action_kind == "lurk"

    def test_eclipse_editor_lurk_fires_in_editor(self):
        s, defn = _load_strategy("snake_eclipse_editor_lurk_default")
        r = s.evaluate(_ctx("eclipse_snake", active_panel="editor"), defn)
        assert r.action_kind == "follow"

    def test_eclipse_problem_view_fires_in_problems(self):
        s, defn = _load_strategy("snake_eclipse_problem_view_default")
        r = s.evaluate(_ctx("eclipse_snake", active_panel="problems"), defn)
        assert r.action_kind == "lurk"

    def test_snake_decisions_are_fast(self):
        import time
        s, defn = _load_strategy("snake_tui_artifact_intent_default")
        start = time.monotonic()
        for _ in range(100):
            s.evaluate(_ctx(selected_artifacts=["X.java"]), defn)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"100 evaluations took {elapsed:.3f}s"


# ── catalog T08.02: E2E Chat bootstrap ───────────────────────────────────────

class TestE2EChatBootstrap:
    def _chat_ctx(self, **kwargs) -> DecisionContext:
        defaults = dict(source_surface="chat_codecompass", ai_status="available")
        defaults.update(kwargs)
        return DecisionContext(**defaults)

    def test_symbol_lookup_opens_ref_with_keywords_and_scope(self):
        s, defn = _load_strategy("chat_codecompass_symbol_lookup_default")
        r = s.evaluate(
            self._chat_ctx(query="class method function interface", allowed_source_scopes=["src"]),
            defn,
        )
        assert r.action_kind == "open_source_ref"

    def test_error_lookup_asks_scope_when_no_refs(self):
        s, defn = _load_strategy("chat_codecompass_error_lookup_default")
        r = s.evaluate(
            self._chat_ctx(query="NullPointerException error crash exception"),
            defn,
        )
        assert r.action_kind == "ask_scope"

    def test_todo_status_shows_context_with_task_scope(self):
        s, defn = _load_strategy("chat_codecompass_todo_status_default")
        r = s.evaluate(
            self._chat_ctx(query="what tasks are in progress todo", allowed_source_scopes=["todo_main"]),
            defn,
        )
        assert r.action_kind == "show_context_summary"

    def test_sourcepack_lookup_opens_ref(self):
        s, defn = _load_strategy("chat_codecompass_sourcepack_lookup_default")
        r = s.evaluate(
            self._chat_ctx(query="api service model", allowed_source_scopes=["service_pack"]),
            defn,
        )
        assert r.action_kind == "open_source_ref"

    def test_all_chat_results_have_heuristic_source(self):
        for hid in [
            "chat_codecompass_symbol_lookup_default",
            "chat_codecompass_error_lookup_default",
            "chat_codecompass_todo_status_default",
            "chat_codecompass_sourcepack_lookup_default",
        ]:
            s, defn = _load_strategy(hid)
            r = s.evaluate(self._chat_ctx(), defn)
            assert r.source == "heuristic", hid


# ── catalog T08.03: E2E Helpcenter and Planning ───────────────────────────────

class TestE2EHelpcenterPlanningBootstrap:
    def test_failure_triage_shows_context_with_scope(self):
        s, defn = _load_strategy("helpcenter_failure_triage_default")
        ctx = DecisionContext(
            source_surface="helpcenter",
            ai_status="available",
            query="build fail regression broken pipeline",
            allowed_source_scopes=["helpcenter_v1"],
        )
        assert s.evaluate(ctx, defn).action_kind == "show_context_summary"

    def test_github_failure_refs_opens_ref_with_git_scope(self):
        s, defn = _load_strategy("helpcenter_github_failure_source_refs_default")
        ctx = DecisionContext(
            source_surface="helpcenter",
            ai_status="available",
            query="github workflow check run pull request",
            allowed_source_scopes=["github_codecompass"],
        )
        assert s.evaluate(ctx, defn).action_kind == "open_source_ref"

    def test_next_task_shows_context_with_todo_scope(self):
        s, defn = _load_strategy("planning_next_task_default")
        ctx = DecisionContext(
            source_surface="planning",
            ai_status="available",
            query="what should I work on next ready unblocked",
            allowed_source_scopes=["todo_main"],
        )
        assert s.evaluate(ctx, defn).action_kind == "show_context_summary"

    def test_archive_done_shows_context_with_scope(self):
        s, defn = _load_strategy("planning_archive_done_default")
        ctx = DecisionContext(
            source_surface="planning",
            ai_status="available",
            query="archive cleanup done finished tidy",
            allowed_source_scopes=["todo_done"],
        )
        assert s.evaluate(ctx, defn).action_kind == "show_context_summary"

    def test_summary_recompute_shows_context_with_goal(self):
        s, defn = _load_strategy("planning_summary_recompute_default")
        ctx = DecisionContext(
            source_surface="planning",
            ai_status="available",
            query="summarize status progress overview",
            active_goal_id="goal_99",
        )
        assert s.evaluate(ctx, defn).action_kind == "show_context_summary"

    def test_related_todo_merge_shows_context_with_todo_scope(self):
        s, defn = _load_strategy("planning_related_todo_merge_default")
        ctx = DecisionContext(
            source_surface="planning",
            ai_status="available",
            query="merge consolidate overlap related",
            allowed_source_scopes=["todo_main"],
        )
        assert s.evaluate(ctx, defn).action_kind == "show_context_summary"


# ── python T08.02: Integration tests with Registry/Loader ─────────────────────

class TestPythonStrategyLoaderIntegration:
    def test_all_bindings_load_successfully(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        bindings = json.load(open(_BINDINGS_PATH))["bindings"]
        loader = PythonStrategyLoader()
        failures = []
        for b in bindings:
            lr = loader.load_module_class(b["module"], b["class"])
            if not lr.success:
                failures.append(f"{b['module']}.{b['class']}: {lr.reason_code}")
        assert not failures, "Failed to load:\n" + "\n".join(failures)

    def test_all_loaded_strategies_are_base_instances(self):
        from agent.heuristics.strategies.base import HeuristicStrategyBase
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        bindings = json.load(open(_BINDINGS_PATH))["bindings"]
        loader = PythonStrategyLoader()
        for b in bindings:
            lr = loader.load_module_class(b["module"], b["class"])
            assert lr.success and isinstance(lr.strategy, HeuristicStrategyBase), b["class"]

    def test_strategy_id_matches_class_name(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        bindings = json.load(open(_BINDINGS_PATH))["bindings"]
        loader = PythonStrategyLoader()
        for b in bindings:
            lr = loader.load_module_class(b["module"], b["class"])
            assert lr.strategy.strategy_id == b["class"], b["class"]

    def test_domain_matches_binding_domain(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        domain_map = {
            "TuiFollowDistanceStrategy": "tui_snake",
            "NoGoodMatchStrategy": "chat_codecompass",
            "FailureTriageStrategy": "helpcenter",
            "NextTaskStrategy": "planning",
            "EclipseEditorLurkStrategy": "snake_eclipse",
        }
        loader = PythonStrategyLoader()
        for b in json.load(open(_BINDINGS_PATH))["bindings"]:
            if b["class"] in domain_map:
                lr = loader.load_module_class(b["module"], b["class"])
                assert lr.strategy.domain() == domain_map[b["class"]], b["class"]

    def test_unknown_module_blocked(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        lr = PythonStrategyLoader().load_module_class("evil.module.path", "EvilStrategy")
        assert not lr.success
        assert "not_allowlisted" in lr.reason_code

    def test_bindings_file_covers_all_python_strategy_bootstraps(self):
        bindings_ids = {b["heuristic_id"] for b in json.load(open(_BINDINGS_PATH))["bindings"]}
        for path in _all_bootstrap_files():
            d = _load(path)
            if d.get("runtime", {}).get("mode") == "python_strategy":
                hid = d["heuristic_id"]
                assert hid in bindings_ids, f"Binding missing for: {hid}"


# ── python T08.03/T08.04: Fallback chains ─────────────────────────────────────

class TestFallbackChains:
    def _chains(self) -> dict:
        return json.load(open(_CHAINS_PATH))["chains"]

    def test_all_domains_have_chains(self):
        expected = {"tui_snake", "snake_eclipse", "chat_codecompass", "helpcenter", "planning"}
        assert expected == set(self._chains().keys())

    def test_each_chain_has_terminal(self):
        for domain, chain in self._chains().items():
            assert "terminal" in chain, f"{domain}: missing terminal"
            assert chain["terminal"] in chain["order"], f"{domain}: terminal not in order"

    def test_chat_chain_ends_with_no_good_match(self):
        chain = self._chains()["chat_codecompass"]
        assert chain["terminal"] == "chat_codecompass_no_good_match_default"
        assert chain["order"][-1] == "chat_codecompass_no_good_match_default"

    def test_all_chain_entries_exist_in_index(self):
        index_ids = {h["heuristic_id"] for h in json.load(open(_INDEX_PATH))["heuristics"]}
        for domain, chain in self._chains().items():
            for hid in chain["order"]:
                assert hid in index_ids, f"{domain}/{hid} not in index"

    def test_snake_tui_chain_starts_with_artifact_intent(self):
        assert self._chains()["tui_snake"]["order"][0] == "snake_tui_artifact_intent_default"

    def test_bindings_cover_all_chain_entries(self):
        bindings_ids = {b["heuristic_id"] for b in json.load(open(_BINDINGS_PATH))["bindings"]}
        for domain, chain in self._chains().items():
            for hid in chain["order"]:
                assert hid in bindings_ids, f"binding missing for {hid}"

    def test_each_chain_order_has_no_duplicates(self):
        for domain, chain in self._chains().items():
            order = chain["order"]
            assert len(order) == len(set(order)), f"{domain}: duplicate entries in chain"
