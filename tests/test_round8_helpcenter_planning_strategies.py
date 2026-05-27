"""Round 8 tests: helpcenter and planning Python strategies + bootstrap JSON files."""
from __future__ import annotations

import json
import os

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


def _def(domain: str = "helpcenter", parameters: dict | None = None) -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id="test",
        version="1.0.0",
        domain=domain,
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
    domain: str = "helpcenter",
    query: str = "",
    scopes: list[str] | None = None,
    artifacts: list[str] | None = None,
    goal_id: str | None = None,
    task_id: str | None = None,
) -> DecisionContext:
    return DecisionContext(
        source_surface=domain,
        ai_status="available",
        query=query or None,
        allowed_source_scopes=scopes or [],
        selected_artifacts=artifacts or [],
        active_goal_id=goal_id,
        active_task_id=task_id,
    )


# ── FailureTriageStrategy ────────────────────────────────────────────────────

class TestFailureTriageStrategy:
    def _s(self):
        from agent.heuristics.strategies.helpcenter.failure_triage import FailureTriageStrategy
        return FailureTriageStrategy()

    def test_no_action_on_unrelated_query(self):
        r = self._s().evaluate(_ctx(query="what is the architecture"), _def())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_failure_but_no_refs(self):
        r = self._s().evaluate(_ctx(query="build fail broken regression"), _def())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_helpcenter_scope(self):
        r = self._s().evaluate(
            _ctx(query="test fail regression broken", scopes=["helpcenter_v1"]),
            _def(),
        )
        assert r.action_kind == "show_context_summary"

    def test_show_context_with_artifact_ref(self):
        r = self._s().evaluate(
            _ctx(query="pipeline fail timeout", artifacts=["build_log.txt"]),
            _def(),
        )
        assert r.action_kind == "show_context_summary"

    def test_reason_includes_score(self):
        r = self._s().evaluate(
            _ctx(query="fail failure broken flaky", scopes=["helpcenter_x"]),
            _def(),
        )
        assert any("failure_triage:score=" in rc for rc in r.reason_codes)


# ── GithubFailureSourceRefsStrategy ──────────────────────────────────────────

class TestGithubFailureSourceRefsStrategy:
    def _s(self):
        from agent.heuristics.strategies.helpcenter.github_failure_refs import (
            GithubFailureSourceRefsStrategy,
        )
        return GithubFailureSourceRefsStrategy()

    def test_no_action_on_non_github_query(self):
        r = self._s().evaluate(_ctx(query="how does authentication work"), _def())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_github_query_no_refs(self):
        r = self._s().evaluate(_ctx(query="pr issue commit branch"), _def())
        assert r.action_kind == "ask_scope"

    def test_open_source_ref_with_github_scope(self):
        r = self._s().evaluate(
            _ctx(query="workflow github action check run", scopes=["github_codecompass"]),
            _def(),
        )
        assert r.action_kind == "open_source_ref"

    def test_open_source_ref_with_git_scope(self):
        r = self._s().evaluate(
            _ctx(query="commit sha branch merge", scopes=["git_history"]),
            _def(),
        )
        assert r.action_kind == "open_source_ref"


# ── DuplicateFailureGroupingStrategy ─────────────────────────────────────────

class TestDuplicateFailureGroupingStrategy:
    def _s(self):
        from agent.heuristics.strategies.helpcenter.duplicate_grouping import (
            DuplicateFailureGroupingStrategy,
        )
        return DuplicateFailureGroupingStrategy()

    def test_no_action_on_unrelated_query(self):
        r = self._s().evaluate(_ctx(query="how to refactor this code"), _def())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_duplicate_query_no_refs(self):
        r = self._s().evaluate(_ctx(query="same issue already known"), _def())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_scope(self):
        r = self._s().evaluate(
            _ctx(query="duplicate similar already seen recurring", scopes=["helpcenter_known_issues"]),
            _def(),
        )
        assert r.action_kind == "show_context_summary"

    def test_reason_includes_scope_count(self):
        r = self._s().evaluate(
            _ctx(query="flaky intermittent recur", scopes=["scope_a", "scope_b"]),
            _def(),
        )
        assert any("scopes:2" in rc for rc in r.reason_codes)


# ── NextTaskStrategy ──────────────────────────────────────────────────────────

class TestNextTaskStrategy:
    def _s(self):
        from agent.heuristics.strategies.planning.next_task import NextTaskStrategy
        return NextTaskStrategy()

    def _pdef(self, params=None):
        return _def(domain="planning", parameters=params)

    def test_no_action_on_unrelated_query(self):
        r = self._s().evaluate(_ctx("planning", "what is dependency injection"), self._pdef())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_next_query_no_scopes(self):
        r = self._s().evaluate(_ctx("planning", "what should I work on next"), self._pdef())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_todo_scopes(self):
        r = self._s().evaluate(
            _ctx("planning", "next ready unblocked priorit", scopes=["todo_main", "task_backlog"]),
            self._pdef(),
        )
        assert r.action_kind == "show_context_summary"

    def test_reason_includes_active_task(self):
        r = self._s().evaluate(
            _ctx("planning", "what should I start next", scopes=["todo_x"], task_id="T42"),
            self._pdef(),
        )
        assert any("active_task:T42" in rc for rc in r.reason_codes)


# ── ArchiveDoneStrategy ───────────────────────────────────────────────────────

class TestArchiveDoneStrategy:
    def _s(self):
        from agent.heuristics.strategies.planning.archive_done import ArchiveDoneStrategy
        return ArchiveDoneStrategy()

    def _pdef(self, params=None):
        return _def(domain="planning", parameters=params)

    def test_no_action_on_non_archive_query(self):
        r = self._s().evaluate(_ctx("planning", "what is the next feature"), self._pdef())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_cleanup_query_no_scopes(self):
        r = self._s().evaluate(_ctx("planning", "cleanup archive done finished"), self._pdef())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_scopes(self):
        r = self._s().evaluate(
            _ctx("planning", "clean archive tidy prune done", scopes=["todo_done"]),
            self._pdef(),
        )
        assert r.action_kind == "show_context_summary"


# ── SummaryRecomputeStrategy ──────────────────────────────────────────────────

class TestSummaryRecomputeStrategy:
    def _s(self):
        from agent.heuristics.strategies.planning.summary_recompute import SummaryRecomputeStrategy
        return SummaryRecomputeStrategy()

    def _pdef(self, params=None):
        return _def(domain="planning", parameters=params)

    def test_no_action_without_summary_keywords(self):
        r = self._s().evaluate(_ctx("planning", "assign this task to me"), self._pdef())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_no_context(self):
        r = self._s().evaluate(_ctx("planning", "status summary progress report"), self._pdef())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_goal(self):
        r = self._s().evaluate(
            _ctx("planning", "summarize progress overview update", goal_id="goal_42"),
            self._pdef(),
        )
        assert r.action_kind == "show_context_summary"

    def test_show_context_with_todo_scope(self):
        r = self._s().evaluate(
            _ctx("planning", "recap status report", scopes=["todo_main"]),
            self._pdef(),
        )
        assert r.action_kind == "show_context_summary"


# ── RelatedTodoMergeStrategy ──────────────────────────────────────────────────

class TestRelatedTodoMergeStrategy:
    def _s(self):
        from agent.heuristics.strategies.planning.related_todo_merge import RelatedTodoMergeStrategy
        return RelatedTodoMergeStrategy()

    def _pdef(self, params=None):
        return _def(domain="planning", parameters=params)

    def test_no_action_on_unrelated_query(self):
        r = self._s().evaluate(_ctx("planning", "how do I run the tests"), self._pdef())
        assert r.action_kind == "no_action"

    def test_ask_scope_when_merge_query_no_scopes(self):
        r = self._s().evaluate(_ctx("planning", "merge combine duplicate related"), self._pdef())
        assert r.action_kind == "ask_scope"

    def test_show_context_with_todo_scopes(self):
        r = self._s().evaluate(
            _ctx("planning", "consolidate group overlap related", scopes=["todo_a", "todo_b"]),
            self._pdef(),
        )
        assert r.action_kind == "show_context_summary"

    def test_reason_includes_selected_count(self):
        r = self._s().evaluate(
            _ctx("planning", "merge combine similar", scopes=["todo_main"], artifacts=["task1", "task2"]),
            self._pdef(),
        )
        assert any("selected:2" in rc for rc in r.reason_codes)


# ── Bootstrap JSON files ──────────────────────────────────────────────────────

_ACTIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "heuristics", "active")

_HELPCENTER_FILES = [
    "helpcenter_failure_triage_default.heuristic.json",
    "helpcenter_github_failure_source_refs_default.heuristic.json",
    "helpcenter_duplicate_failure_grouping_default.heuristic.json",
]

_PLANNING_FILES = [
    "planning_next_task_default.heuristic.json",
    "planning_archive_done_default.heuristic.json",
    "planning_summary_recompute_default.heuristic.json",
    "planning_related_todo_merge_default.heuristic.json",
]

_ALL_NEW_FILES = _HELPCENTER_FILES + _PLANNING_FILES


class TestRound8BootstrapFiles:
    def _load(self, fn: str) -> dict:
        return json.load(open(os.path.join(_ACTIVE_DIR, fn)))

    def test_all_files_exist(self):
        for fn in _ALL_NEW_FILES:
            assert os.path.exists(os.path.join(_ACTIVE_DIR, fn)), f"Missing: {fn}"

    def test_all_are_deterministic(self):
        for fn in _ALL_NEW_FILES:
            assert self._load(fn).get("deterministic") is True, fn

    def test_all_have_readonly_safety(self):
        for fn in _ALL_NEW_FILES:
            assert self._load(fn).get("safety_class") == "readonly", fn

    def test_all_use_python_strategy_mode(self):
        for fn in _ALL_NEW_FILES:
            d = self._load(fn)
            assert d["runtime"]["mode"] == "python_strategy", fn

    def test_ttl_in_valid_range(self):
        for fn in _ALL_NEW_FILES:
            ttl = self._load(fn).get("ttl_policy", {})
            assert ttl.get("min_seconds", 0) >= 10.0, fn
            assert ttl.get("max_seconds", 0) <= 20.0, fn

    def test_no_forbidden_capabilities(self):
        forbidden = {"file_write", "network_access", "secret_access"}
        for fn in _ALL_NEW_FILES:
            bad = set(self._load(fn).get("capabilities", [])) & forbidden
            assert not bad, f"{fn}: {bad}"

    def test_python_strategy_modules_allowlisted(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        loader = PythonStrategyLoader()
        for fn in _ALL_NEW_FILES:
            d = self._load(fn)
            mod = d["runtime"]["python_strategy"]["module"]
            cls = d["runtime"]["python_strategy"]["class"]
            assert loader.is_allowlisted(mod, cls), f"{fn}: {mod}.{cls}"

    def test_index_includes_all_entries(self):
        index = json.load(
            open(os.path.join(os.path.dirname(__file__), "..", "heuristics", "index.json"))
        )
        ids = {h["heuristic_id"] for h in index["heuristics"]}
        for fn in _ALL_NEW_FILES:
            hid = fn.replace(".heuristic.json", "")
            assert hid in ids, f"index.json missing: {hid}"

    def test_helpcenter_domain_correct(self):
        for fn in _HELPCENTER_FILES:
            assert self._load(fn).get("domain") == "helpcenter", fn

    def test_planning_domain_correct(self):
        for fn in _PLANNING_FILES:
            assert self._load(fn).get("domain") == "planning", fn
