"""
Tests for the generic Source-First Selector in RepositoryMapEngine.search().

The Source-First Selector must work for ANY query token, not just for
"codecompass". The original bug was: test files with names like
test_<topic>_*.py accumulated more test_<topic>_* symbol hits than
source files had *<topic>* symbols, so test files outranked source
files in the repository map. The keystone fix is the stem-token
boost on source files (tests/test_repository_map_source_first_selector.py).

This file covers two additional generic correctness rules:

1. Vendored source trees are classified from universal directory
   conventions rather than from repository-specific allowlists.

2. Frontend test files (*.spec.ts, *.spec.js) must be detected as
   tests so they do not outrank the components they verify for
   Angular/Karma queries like "zeig mir die api routes".

3. Non-source top-level directories (docs/, data/, venv/, node_modules/,
   __pycache__/, …) are not source code at all and must be demoted.
"""
from __future__ import annotations

from pathlib import Path

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

def test_conventional_vendor_tree_is_demoted_below_production_source():
    """
    A conventional vendor tree must not outrank production code merely
    because both paths contain the same query token.
    """
    engine = _make_engine_with_symbols({
        "src/routes/tasks/management.py": [
            "create_task", "list_tasks", "task_admin_service",
        ],
        "vendor/blender/addon/tasks.py": [
            "blender_task_callback", "register_tasks_panel",
        ],
        "third_party/freecad/workbench/tasks.py": [
            "freecad_task_handler",
        ],
    })

    results = engine.search("erkläre tasks", top_k=5)
    paths = [r.source for r in results]

    assert paths[0] == "src/routes/tasks/management.py", (
        f"top-1 must be production source, got: {paths[0]}"
    )
    production_score = next(r.score for r in results if r.source == "src/routes/tasks/management.py")
    for r in results:
        if r.source.startswith(("vendor/", "third_party/")):
            assert production_score > r.score, (
                f"production ({production_score}) must outrank vendored "
                f"{r.source} ({r.score})"
            )


def test_unknown_top_level_directories_are_role_neutral_and_deterministic():
    """
    Unknown repository directories are neutral. Equal evidence is resolved
    by the canonical path rather than a product-specific allowlist.
    """
    engine = _make_engine_with_symbols({
        "extensions/alpha/snake_persistence.py": [
            "save_snake_state", "load_snake_state",
        ],
        "modules/beta/snake_persistence.py": [
            "save_snake_state", "load_snake_state",
        ],
    })

    results = engine.search("snake persistence", top_k=5)
    paths = [r.source for r in results]

    assert paths == sorted(paths)
    assert {result.metadata["file_role"] for result in results} == {"production"}


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
