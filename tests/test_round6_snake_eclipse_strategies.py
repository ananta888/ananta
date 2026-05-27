"""Round 6 tests: TuiArtifactIntentStrategy, TuiDiffFocusStrategy, Eclipse strategies,
and their bootstrap JSON files."""
from __future__ import annotations

import json
import os
import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


def _make_def(
    heuristic_id: str = "test",
    domain: str = "tui_snake",
    parameters: dict | None = None,
) -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id=heuristic_id,
        version="1.0.0",
        domain=domain,
        strategy_kind="follow",
        description="test",
        deterministic=True,
        safety_class="ui_motion_only",
        capabilities=(),
        inputs=(),
        outputs=(),
        parameters=parameters or {},
    )


def _make_ctx(**kwargs) -> DecisionContext:
    defaults = dict(source_surface="tui_snake", ai_status="offline")
    defaults.update(kwargs)
    return DecisionContext(**defaults)


# ── TuiArtifactIntentStrategy ────────────────────────────────────────────────

class TestTuiArtifactIntentStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_tui.artifact_intent import TuiArtifactIntentStrategy
        return TuiArtifactIntentStrategy()

    def test_lurk_when_no_artifact(self):
        s = self._strategy()
        ctx = _make_ctx(selected_artifacts=[])
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "lurk"

    def test_follow_when_artifact_selected(self):
        s = self._strategy()
        ctx = _make_ctx(selected_artifacts=["MyClass.java"])
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "follow"
        assert result.confidence == 0.9

    def test_follow_when_artifact_event(self):
        s = self._strategy()
        ctx = _make_ctx(
            selected_artifacts=[],
            recent_events=[{"event_type": "artifact_selected"}],
        )
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "follow"

    def test_reason_code_contains_artifact_ref(self):
        s = self._strategy()
        ctx = _make_ctx(selected_artifacts=["SomeFile.py"])
        result = s.evaluate(ctx, _make_def())
        assert any("artifact_intent:" in rc for rc in result.reason_codes)

    def test_deterministic_same_artifact_same_motion(self):
        s = self._strategy()
        ctx = _make_ctx(selected_artifacts=["stable_artifact"])
        r1 = s.evaluate(ctx, _make_def())
        r2 = s.evaluate(ctx, _make_def())
        assert r1.suggested_motion == r2.suggested_motion


# ── TuiDiffFocusStrategy ─────────────────────────────────────────────────────

class TestTuiDiffFocusStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_tui.diff_focus import TuiDiffFocusStrategy
        return TuiDiffFocusStrategy()

    def test_lurk_in_diff_panel(self):
        s = self._strategy()
        ctx = _make_ctx(active_panel="diff_view")
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "lurk"
        assert result.confidence == 0.95

    def test_lurk_in_compare_panel(self):
        s = self._strategy()
        ctx = _make_ctx(active_panel="git_compare")
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "lurk"

    def test_lurk_on_diff_event(self):
        s = self._strategy()
        ctx = _make_ctx(
            active_panel="editor",
            recent_events=[{"event_type": "diff_opened"}],
        )
        result = s.evaluate(ctx, _make_def())
        assert result.action_kind == "lurk"

    def test_fallback_when_not_in_diff(self):
        s = self._strategy()
        ctx = _make_ctx(active_panel="terminal", recent_events=[])
        result = s.evaluate(ctx, _make_def())
        assert result.fallback_reason == "not_in_diff_context"


# ── EclipseEditorLurkStrategy ────────────────────────────────────────────────

class TestEclipseEditorLurkStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_eclipse.editor_lurk import EclipseEditorLurkStrategy
        return EclipseEditorLurkStrategy()

    def _eclipse_def(self, params=None):
        return _make_def(
            heuristic_id="eclipse_editor_lurk_test",
            domain="snake_eclipse",
            parameters=params or {},
        )

    def _ctx(self, **kw):
        defaults = dict(source_surface="eclipse_snake", ai_status="offline")
        defaults.update(kw)
        return DecisionContext(**defaults)

    def test_follow_in_editor_panel(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "follow"

    def test_follow_in_source_panel(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="source_editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "follow"

    def test_fallback_when_not_editor(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="problems")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.fallback_reason == "not_in_editor_zone"

    def test_default_motion_is_rightward(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.suggested_motion is not None
        assert result.suggested_motion.dx == 1


# ── EclipseProblemViewStrategy ───────────────────────────────────────────────

class TestEclipseProblemViewStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_eclipse.problem_view import EclipseProblemViewStrategy
        return EclipseProblemViewStrategy()

    def _eclipse_def(self):
        return _make_def(heuristic_id="eclipse_problems_test", domain="snake_eclipse")

    def _ctx(self, **kw):
        defaults = dict(source_surface="eclipse_snake", ai_status="offline")
        defaults.update(kw)
        return DecisionContext(**defaults)

    def test_lurk_in_problems_panel(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="problems")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "lurk"

    def test_lurk_in_error_log(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="error_log")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "lurk"

    def test_fallback_in_editor(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.fallback_reason == "not_in_problems_zone"

    def test_reason_includes_zone(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="problems")
        result = s.evaluate(ctx, self._eclipse_def())
        assert any("eclipse_problems" in rc for rc in result.reason_codes)


# ── EclipseCompareStrategy ───────────────────────────────────────────────────

class TestEclipseCompareStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_eclipse.compare import EclipseCompareStrategy
        return EclipseCompareStrategy()

    def _eclipse_def(self, params=None):
        return _make_def(
            heuristic_id="eclipse_compare_test",
            domain="snake_eclipse",
            parameters=params or {},
        )

    def _ctx(self, **kw):
        defaults = dict(source_surface="eclipse_snake", ai_status="offline")
        defaults.update(kw)
        return DecisionContext(**defaults)

    def test_follow_in_compare_panel(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="compare")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "follow"

    def test_follow_in_diff_panel(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="diff")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "follow"

    def test_right_side_gives_dx_positive(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="git_compare")
        result = s.evaluate(ctx, self._eclipse_def({"right_side": True}))
        assert result.suggested_motion.dx == 1

    def test_left_side_gives_dx_negative(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="git_compare")
        result = s.evaluate(ctx, self._eclipse_def({"right_side": False}))
        assert result.suggested_motion.dx == -1

    def test_fallback_when_not_compare(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.fallback_reason == "not_in_compare_zone"


# ── EclipsePackageExplorerStrategy ───────────────────────────────────────────

class TestEclipsePackageExplorerStrategy:
    def _strategy(self):
        from agent.heuristics.strategies.snake_eclipse.package_explorer import EclipsePackageExplorerStrategy
        return EclipsePackageExplorerStrategy()

    def _eclipse_def(self):
        return _make_def(heuristic_id="eclipse_pkg_test", domain="snake_eclipse")

    def _ctx(self, **kw):
        defaults = dict(source_surface="eclipse_snake", ai_status="offline")
        defaults.update(kw)
        return DecisionContext(**defaults)

    def test_lurk_in_package_explorer(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="package_explorer")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "lurk"

    def test_lurk_in_project_explorer(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="project_explorer")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.action_kind == "lurk"

    def test_fallback_when_not_explorer(self):
        s = self._strategy()
        ctx = self._ctx(active_panel="editor")
        result = s.evaluate(ctx, self._eclipse_def())
        assert result.fallback_reason == "not_in_package_explorer"

    def test_reason_includes_artifact_when_selected(self):
        s = self._strategy()
        ctx = self._ctx(
            active_panel="package_explorer",
            selected_artifacts=["com.example.MyClass"],
        )
        result = s.evaluate(ctx, self._eclipse_def())
        assert any("selected:" in rc for rc in result.reason_codes)


# ── Bootstrap JSON files ──────────────────────────────────────────────────────

_HEURISTICS_DIR = os.path.join(os.path.dirname(__file__), "..", "heuristics", "active")

_ECLIPSE_FILES = [
    "snake_tui_artifact_intent_default.heuristic.json",
    "snake_tui_diff_focus_default.heuristic.json",
    "snake_eclipse_editor_lurk_default.heuristic.json",
    "snake_eclipse_problem_view_default.heuristic.json",
    "snake_eclipse_compare_default.heuristic.json",
    "snake_eclipse_package_explorer_default.heuristic.json",
]


class TestRound6BootstrapFiles:
    def _load(self, filename: str) -> dict:
        path = os.path.join(_HEURISTICS_DIR, filename)
        with open(path) as f:
            return json.load(f)

    def test_all_new_files_exist(self):
        for fn in _ECLIPSE_FILES:
            path = os.path.join(_HEURISTICS_DIR, fn)
            assert os.path.exists(path), f"Missing: {fn}"

    def test_all_are_valid_json(self):
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            assert isinstance(data, dict)

    def test_all_are_deterministic(self):
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            assert data.get("deterministic") is True, f"{fn}: deterministic must be true"

    def test_all_have_ui_motion_only_safety(self):
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            assert data.get("safety_class") == "ui_motion_only", (
                f"{fn}: expected ui_motion_only, got {data.get('safety_class')}"
            )

    def test_all_use_python_strategy_mode(self):
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            mode = data.get("runtime", {}).get("mode")
            assert mode == "python_strategy", f"{fn}: expected python_strategy, got {mode}"

    def test_python_strategy_modules_are_allowlisted(self):
        from agent.services.heuristic_runtime.python_strategy_loader import PythonStrategyLoader
        loader = PythonStrategyLoader()
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            module = data["runtime"]["python_strategy"]["module"]
            cls = data["runtime"]["python_strategy"]["class"]
            assert loader.is_allowlisted(module, cls), f"{fn}: not allowlisted: {module}.{cls}"

    def test_no_forbidden_capabilities(self):
        forbidden = {"file_write", "network_access", "secret_access"}
        for fn in _ECLIPSE_FILES:
            data = self._load(fn)
            caps = set(data.get("capabilities", []))
            bad = caps & forbidden
            assert not bad, f"{fn}: forbidden capabilities: {bad}"

    def test_index_includes_all_new_entries(self):
        index_path = os.path.join(os.path.dirname(__file__), "..", "heuristics", "index.json")
        index = json.load(open(index_path))
        ids = {h["heuristic_id"] for h in index["heuristics"]}
        for fn in _ECLIPSE_FILES:
            hid = fn.replace(".heuristic.json", "")
            assert hid in ids, f"index.json missing: {hid}"
