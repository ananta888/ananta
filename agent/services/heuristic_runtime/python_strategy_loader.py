"""PythonStrategyLoader — loads and validates Python HeuristicStrategy classes.

Only explicitly allowlisted module prefixes are permitted.
Inline Python code in heuristic JSON is forbidden.
Dynamic imports outside allowed modules are blocked.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

# Only these module prefixes may host Python strategies
_ALLOWED_MODULE_PREFIXES: tuple[str, ...] = (
    "agent.heuristics.strategies.",
    "agent.services.heuristic_runtime.",
)

# Explicit allowlist: module -> set of allowed class names
_STRATEGY_ALLOWLIST: dict[str, set[str]] = {
    "agent.heuristics.strategies.snake_tui.follow_distance": {"TuiFollowDistanceStrategy"},
    "agent.heuristics.strategies.snake_tui.lurk_focus": {"TuiLurkFocusStrategy"},
    "agent.heuristics.strategies.snake_tui.artifact_intent": {"TuiArtifactIntentStrategy"},
    "agent.heuristics.strategies.snake_tui.diff_focus": {"TuiDiffFocusStrategy"},
    "agent.heuristics.strategies.snake_eclipse.editor_lurk": {"EclipseEditorLurkStrategy"},
    "agent.heuristics.strategies.snake_eclipse.problem_view": {"EclipseProblemViewStrategy"},
    "agent.heuristics.strategies.snake_eclipse.compare": {"EclipseCompareStrategy"},
    "agent.heuristics.strategies.snake_eclipse.package_explorer": {"EclipsePackageExplorerStrategy"},
    "agent.heuristics.strategies.chat_codecompass.selected_artifact_first": {"SelectedArtifactFirstStrategy"},
    "agent.heuristics.strategies.chat_codecompass.symbol_lookup": {"SymbolLookupStrategy"},
    "agent.heuristics.strategies.chat_codecompass.error_lookup": {"ErrorLookupStrategy"},
    "agent.heuristics.strategies.chat_codecompass.todo_status": {"TodoStatusStrategy"},
    "agent.heuristics.strategies.chat_codecompass.sourcepack_lookup": {"SourcePackLookupStrategy"},
    "agent.heuristics.strategies.chat_codecompass.no_good_match": {"NoGoodMatchStrategy"},
    "agent.heuristics.strategies.helpcenter.failure_triage": {"FailureTriageStrategy"},
    "agent.heuristics.strategies.helpcenter.github_failure_refs": {"GithubFailureSourceRefsStrategy"},
    "agent.heuristics.strategies.helpcenter.duplicate_grouping": {"DuplicateFailureGroupingStrategy"},
    "agent.heuristics.strategies.planning.next_task": {"NextTaskStrategy"},
    "agent.heuristics.strategies.planning.archive_done": {"ArchiveDoneStrategy"},
    "agent.heuristics.strategies.planning.summary_recompute": {"SummaryRecomputeStrategy"},
    "agent.heuristics.strategies.planning.related_todo_merge": {"RelatedTodoMergeStrategy"},
}


@dataclass
class LoadResult:
    success: bool
    strategy: Any | None = None  # HeuristicStrategyBase instance or None
    reason_code: str = ""


class PythonStrategyLoadError(ValueError):
    pass


class PythonStrategyLoader:
    """Loads Python strategy classes from the allowlist.

    Inline code in JSON, unknown modules, and non-allowlisted classes are all rejected.
    """

    def __init__(self, allowlist: dict[str, set[str]] | None = None) -> None:
        self._allowlist = allowlist if allowlist is not None else _STRATEGY_ALLOWLIST

    def load(self, hdef: HeuristicDefinition) -> LoadResult:
        """Load and instantiate the Python strategy for a HeuristicDefinition."""
        runtime = dict(hdef.parameters.get("runtime") or {})
        if runtime.get("mode") != "python_strategy":
            return LoadResult(success=False, reason_code="not_python_strategy_mode")

        ps = dict(runtime.get("python_strategy") or {})
        module_path = str(ps.get("module") or "").strip()
        class_name = str(ps.get("class") or "").strip()

        if not module_path or not class_name:
            return LoadResult(success=False, reason_code="missing_module_or_class")

        # Allowlist check
        allowed_classes = self._allowlist.get(module_path)
        if allowed_classes is None:
            return LoadResult(success=False, reason_code=f"module_not_allowlisted:{module_path}")
        if class_name not in allowed_classes:
            return LoadResult(success=False, reason_code=f"class_not_allowlisted:{class_name}")

        # Module prefix check (belt + suspenders)
        if not any(module_path.startswith(p) for p in _ALLOWED_MODULE_PREFIXES):
            return LoadResult(success=False, reason_code=f"module_prefix_blocked:{module_path}")

        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            instance = cls()
            return LoadResult(success=True, strategy=instance)
        except (ImportError, AttributeError) as exc:
            return LoadResult(success=False, reason_code=f"import_error:{exc}")
        except Exception as exc:
            return LoadResult(success=False, reason_code=f"instantiation_error:{exc}")

    def is_allowlisted(self, module_path: str, class_name: str) -> bool:
        classes = self._allowlist.get(module_path)
        return classes is not None and class_name in classes

    def all_allowlisted(self) -> list[tuple[str, str]]:
        return [
            (mod, cls)
            for mod, classes in self._allowlist.items()
            for cls in classes
        ]
