from __future__ import annotations

import fnmatch
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

FILE_TO_SCOPE_MAP: tuple[tuple[str, Optional[str]], ...] = (
    ("agent/services/goal_config*", "goal-config"),
    ("agent/routes/tasks/goals*", "goal-config"),
    ("tests/test_goal_config*", "goal-config"),
    ("tests/test_goal_scoped*", "goal-config"),
    ("agent/services/config_profile*", "profiles"),
    ("tests/test_config_profile*", "profiles"),
    ("agent/llm_integration*", "llm"),
    ("agent/services/task_scoped_execution*", "llm"),
    ("agent/services/llm_response*", "llm"),
    ("tests/test_llm*", "llm"),
    ("scripts/ollama-autoimport*", "modelfile"),
    ("autoimport-state/modelfiles/**", "modelfile"),
    ("agent/services/autopilot*", "autopilot"),
    ("agent/services/auto_planner*", "autopilot"),
    ("tests/test_auto*", "autopilot"),
    ("agent/services/commit_*", "commit"),
    ("tests/test_commit*", "commit"),
    ("agent/services/planning_*", "planning"),
    ("tests/test_planning*", "planning"),
    ("agent/services/context_delivery*", "context"),
    ("agent/services/workspace_context_policy*", "context"),
    ("tests/test_context_*", "context"),
    ("agent/services/worker_workspace*", "workspace"),
    ("agent/services/workspace_*", "workspace"),
    ("agent/services/acceptance_runner*", "runner"),
    ("tests/test_acceptance*", "runner"),
    ("agent/services/ssh*", "ssh"),
    ("agent/routes/tasks/ssh*", "ssh"),
    ("agent/services/goal_*", "goal"),
    ("agent/routes/tasks/*", "api"),
    ("agent/routes/*", "api"),
    ("agent/models.py", "schema"),
    ("agent/task_models.py", "schema"),
    ("agent/db_models.py", "schema"),
    ("agent/runtime_policy.py", "routing"),
    ("agent/services/platform_governance*", "governance"),
    ("agent/services/rag*", "rag"),
    ("agent/services/codecompass*", "rag"),
    ("agent/tools.py", "tools"),
    ("AGENTS.md", "docs"),
    ("CONTRIBUTING.md", "docs"),
    ("*.md", "docs"),
    ("docker-compose*", "ci"),
    (".github/**", "ci"),
    (".commitlintrc*", "ci"),
    ("agent/config*", "config"),
    ("tests/**", None),
)


@dataclass(frozen=True)
class ScopeResolution:
    primary_scope: Optional[str]
    all_scopes: list[str]
    is_mixed: bool
    unresolved_paths: list[str]


class CommitScopeResolver:
    def __init__(
        self,
        scope_map: tuple[tuple[str, Optional[str]], ...] | None = None,
    ) -> None:
        self._map = scope_map if scope_map is not None else FILE_TO_SCOPE_MAP

    def _resolve_one(self, path: str) -> Optional[str]:
        normalized = path.lstrip("/").replace("\\", "/")
        for pattern, scope in self._map:
            if fnmatch.fnmatch(normalized, pattern):
                return scope
        return None

    def resolve(self, file_paths: list[str]) -> ScopeResolution:
        if not file_paths:
            return ScopeResolution(
                primary_scope=None,
                all_scopes=[],
                is_mixed=False,
                unresolved_paths=[],
            )

        scope_hits: list[str] = []
        unresolved: list[str] = []

        for path in file_paths:
            scope = self._resolve_one(path)
            if scope is None:
                unresolved.append(path)
            else:
                scope_hits.append(scope)

        if not scope_hits:
            return ScopeResolution(
                primary_scope=None,
                all_scopes=[],
                is_mixed=False,
                unresolved_paths=unresolved,
            )

        counter = Counter(scope_hits)
        unique_scopes = list(dict.fromkeys(scope_hits))
        primary = counter.most_common(1)[0][0]
        is_mixed = len(counter) > 1

        return ScopeResolution(
            primary_scope=primary,
            all_scopes=unique_scopes,
            is_mixed=is_mixed,
            unresolved_paths=unresolved,
        )


_RESOLVER = CommitScopeResolver()


def get_commit_scope_resolver() -> CommitScopeResolver:
    return _RESOLVER
