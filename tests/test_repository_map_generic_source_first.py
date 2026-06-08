"""
Tests for the generic Source-First Selector in RepositoryMapEngine.search().

The Source-First Selector must work for ANY query token, not just for
"codecompass". The original bug was: test files with names like
test_<topic>_*.py accumulated more test_<topic>_* symbol hits than
source files had *<topic>* symbols, so test files outranked source
files in the repository map. The keystone fix is the stem-token
boost on source files (tests/test_repository_map_source_first_selector.py).

This file covers two additional generic correctness rules:

1. Third-party client_surfaces (blender/, freecad/, eclipse_runtime/,
   nvim_runtime/, vim_compat/, vscode_extension/) must not outrank
   Ananta-core source files when both match the same token by accident
   (e.g. blender/addon/tasks.py has nothing to do with Ananta's task
   system even though the filename matches).

2. Frontend test files (*.spec.ts, *.spec.js) must be detected as
   tests so they do not outrank the components they verify for
   Angular/Karma queries like "zeig mir die api routes".

3. Non-source top-level directories (docs/, data/, venv/, node_modules/,
   __pycache__/, …) are not source code at all and must be demoted.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

# Stub tree-sitter so the engine's import path is satisfied (the engine
# falls back to regex-based symbol extraction when the parser module
# is absent, which is what these tests need).
sys.modules.setdefault("tree_sitter", types.ModuleType("tree_sitter"))
sys.modules.setdefault("tree_sitter_python", types.ModuleType("tree_sitter_python"))

from agent.hybrid_orchestrator import RepositoryMapEngine


def _make_engine_with_symbols(symbol_graph: dict[str, list[str]]) -> RepositoryMapEngine:
    engine = RepositoryMapEngine(
        repo_root="/tmp/ananta-source-first-selector-generic",
        max_files=10_000,
        max_symbols_per_file=200,
    )
    engine._symbol_graph = dict(symbol_graph)
    return engine


# --- Third-party client_surfaces ------------------------------------------

def test_third_party_client_surface_demoted_below_ananta_core():
    """
    Query "tasks" must return Ananta's task system on top, not
    blender/addon/tasks.py or freecad/workbench/tasks.py which are
    third-party integrations that happen to be named "tasks".
    """
    engine = _make_engine_with_symbols({
        # Ananta-core source: token in stem
        "agent/routes/tasks/management.py": [
            "create_task", "list_tasks", "task_admin_service",
        ],
        # Third-party blender integration: same token in stem
        "client_surfaces/blender/addon/tasks.py": [
            "blender_task_callback", "register_tasks_panel",
        ],
        # Third-party freecad integration: same token in stem
        "client_surfaces/freecad/workbench/tasks.py": [
            "freecad_task_handler",
        ],
    })

    results = engine.search("erkläre tasks", top_k=5)
    paths = [r.source for r in results]

    assert paths[0] == "agent/routes/tasks/management.py", (
        f"top-1 must be Ananta-core, got: {paths[0]}"
    )
    # The Ananta-core file must score strictly above every third-party
    # file that matched the same token by accident. The ×0.2 demote on
    # third-party client_surfaces guarantees this.
    ananta_score = next(r.score for r in results if r.source == "agent/routes/tasks/management.py")
    for r in results:
        if r.source.startswith(("client_surfaces/blender/", "client_surfaces/freecad/")):
            assert ananta_score > r.score, (
                f"Ananta-core ({ananta_score}) must outrank third-party "
                f"{r.source} ({r.score})"
            )


def test_ananta_client_surface_not_demoted():
    """
    Ananta's own client_surfaces subdirs (operator_tui/, tui_runtime/,
    common/) must NOT be demoted — they are first-party code shipped
    with Ananta.
    """
    engine = _make_engine_with_symbols({
        "client_surfaces/operator_tui/snake_persistence.py": [
            "save_snake_state", "load_snake_state",
        ],
        "client_surfaces/blender/addon/snake_persistence.py": [
            "blender_snake_persistence_bridge",
        ],
    })

    results = engine.search("snake persistence", top_k=5)
    paths = [r.source for r in results]

    # The operator_tui file is first-party and must rank above the
    # blender bridge which is third-party.
    assert paths[0] == "client_surfaces/operator_tui/snake_persistence.py", (
        f"operator_tui (Ananta) must outrank blender (third-party): {paths[0]}"
    )


# --- Frontend test files -------------------------------------------------

def test_angular_spec_ts_detected_as_test():
    """
    *.spec.ts files in frontend-angular/ are Angular/Karma test files.
    They must be detected as tests and demoted below the real
    components for queries like "zeig mir die api routes".
    """
    engine = _make_engine_with_symbols({
        # Real Angular routes module
        "frontend-angular/src/app/app.routes.ts": [
            "AppRoutingModule", "routes", "RouteConfig",
        ],
        # Angular test for app.routes
        "frontend-angular/src/app/app.routes.spec.ts": [
            "should configure routes", "should redirect unauthenticated",
        ],
    })

    results = engine.search("zeig mir die api routes", top_k=5)
    paths = [r.source for r in results]

    # The real routes module must be top-1
    assert paths[0] == "frontend-angular/src/app/app.routes.ts", (
        f"real routes module must be top-1, got: {paths[0]}"
    )
    # The .spec.ts file must appear lower in the ranking
    spec_idx = paths.index("frontend-angular/src/app/app.routes.spec.ts")
    assert spec_idx > 0, f".spec.ts must not be top-1: {paths}"


# --- Non-source top-level directories ------------------------------------

def test_docs_demoted_below_source():
    """
    docs/ files (markdown documentation) must be demoted relative to
    actual source files when a query matches both. docs/ is
    documentation ABOUT the system, not the system itself.
    """
    engine = _make_engine_with_symbols({
        "agent/services/heuristic_runtime_service.py": [
            "HeuristicRuntime", "evaluate",
        ],
        "docs/heuristics.md": [
            "Heuristik", "Heuristik Dokumentation", "evaluate heuristic",
        ],
    })

    results = engine.search("heuristic runtime", top_k=5)
    paths = [r.source for r in results]

    # The source file must be top-1; docs is documentation, not the
    # implementation
    assert paths[0] == "agent/services/heuristic_runtime_service.py", (
        f"source must be top-1, got: {paths[0]}"
    )


def test_venv_and_node_modules_demoted():
    """
    Virtualenvs, node_modules, and __pycache__ must be demoted —
    they are dependency/build artifacts, not source code. They
    should not appear in the top results for architectural queries.
    """
    engine = _make_engine_with_symbols({
        "agent/services/scheduler.py": [
            "Scheduler", "tick", "schedule",
        ],
        "venv/lib/python3.12/site-packages/requests/api.py": [
            "requests_get", "requests_post", "schedule_request",
        ],
        "node_modules/lodash/schedule.js": [
            "schedule", "debounce",
        ],
    })

    results = engine.search("scheduler", top_k=5)
    paths = [r.source for r in results]

    assert paths[0] == "agent/services/scheduler.py", (
        f"scheduler source must be top-1, got: {paths[0]}"
    )
    # Dependency files must not appear in top-3
    for p in paths[:3]:
        assert "venv/" not in p, f"venv in top-3: {p}"
        assert "node_modules/" not in p, f"node_modules in top-3: {p}"


# --- Multi-domain queries ------------------------------------------------

def test_query_matches_multiple_ananta_domains_correctly():
    """
    A query that mentions multiple domain tokens must let each token
    contribute to the score, but the Source-First Selector must still
    demote test files that do not match any domain in their stem.
    """
    engine = _make_engine_with_symbols({
        # Source: both tokens in stem
        "agent/services/scheduler_runtime_service.py": [
            "SchedulerRuntime", "tick",
        ],
        # Source: only one token in stem
        "agent/services/runtime_metrics_service.py": [
            "RuntimeMetrics", "collect",
        ],
        # Test: one token in stem, another in symbols
        "tests/test_scheduler_tick_rate.py": [
            "test_scheduler_tick_rate", "test_runtime_metrics_in_logs",
        ],
    })

    results = engine.search("scheduler runtime", top_k=5)
    paths = [r.source for r in results]

    # Source files must outrank the test file
    source_idx = [
        i for i, p in enumerate(paths)
        if not p.startswith("tests/") and not Path(p).stem.startswith("test_")
    ]
    test_idx = [
        i for i, p in enumerate(paths)
        if p.startswith("tests/") or Path(p).stem.startswith("test_")
    ]
    assert source_idx and test_idx, f"missing source/test: {paths}"
    assert min(source_idx) < min(test_idx), (
        f"test file outranks source for 'scheduler runtime': {paths}"
    )
