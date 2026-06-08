"""
Test for the Source-First Selector in RepositoryMapEngine.search().

Bug: for the query "erkläre mir den codecompass" the top-1 result was
`tests/test_codecompass_trigger_mode.py` (score 8.4) while
`worker/retrieval/codecompass_budgeting.py` scored 3.4. Test files
accumulate more `test_codecompass_*` symbol hits than source files
have `*codecompass*` symbol hits, so test files beat source files in
the repository map. The fix boosts source files whose filename stem
contains a domain token from the query, and excludes test paths from
that boost.

This test exercises the ranking in isolation with a synthetic symbol
graph (no filesystem dependency).
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

# Stub the tree-sitter import to avoid a hard dependency for this test
# (the engine falls back to regex-based symbol extraction when the
# parser module is absent).
sys.modules.setdefault("tree_sitter", types.ModuleType("tree_sitter"))
sys.modules.setdefault("tree_sitter_python", types.ModuleType("tree_sitter_python"))

from agent.hybrid_orchestrator import RepositoryMapEngine


def _make_engine_with_symbols(symbol_graph: dict[str, list[str]]) -> RepositoryMapEngine:
    """Build a RepositoryMapEngine preloaded with a synthetic symbol graph."""
    engine = RepositoryMapEngine(
        repo_root="/tmp/ananta-source-first-selector",
        max_files=10_000,
        max_symbols_per_file=200,
    )
    # The production path uses build() which scans the filesystem. We
    # bypass that here because the test is about ranking, not scanning.
    engine._symbol_graph = dict(symbol_graph)
    return engine


def test_codecompass_query_promotes_source_over_test_files():
    """
    The query "erkläre mir den codecompass" must rank
    `worker/retrieval/codecompass_budgeting.py` above
    `tests/test_codecompass_trigger_mode.py` even though the test file
    has more `codecompass` symbol hits.
    """
    engine = _make_engine_with_symbols({
        # Real test file — 6 symbols containing 'codecompass'
        "tests/test_codecompass_trigger_mode.py": [
            "test_codecompass_flags_are_independent",
            "test_codecompass_relative_path_is_logged",
            "test_codecompass_trigger_mode_default",
            "test_codecompass_disabled_returns_empty_budget",
            "test_codecompass_budget_profile_keys",
            "TestTriggerModeAuto",
        ],
        # Real source file — 2 symbols containing 'codecompass'
        "worker/retrieval/codecompass_budgeting.py": [
            "resolve_codecompass_budget",
            "apply_codecompass_budget",
        ],
        # Real source file — 1 symbol
        "agent/services/codecompass_output_reader.py": [
            "get_codecompass_output_reader",
        ],
    })

    results = engine.search("erkläre mir den codecompass", top_k=5)
    paths = [r.source for r in results]

    # Source files must be ranked above test files
    source_indices = [
        i for i, p in enumerate(paths)
        if not p.startswith("tests/") and not Path(p).stem.startswith("test_")
    ]
    test_indices = [
        i for i, p in enumerate(paths)
        if p.startswith("tests/") or Path(p).stem.startswith("test_")
    ]
    assert source_indices, f"no source file in top-5: {paths}"
    assert test_indices, f"no test file in top-5: {paths}"
    assert min(source_indices) < min(test_indices), (
        f"test files outrank source files for 'codecompass' query: {paths}"
    )

    # And the actual top-1 should be a source file
    assert not (paths[0].startswith("tests/") or Path(paths[0]).stem.startswith("test_")), (
        f"top-1 is a test file: {paths[0]}"
    )


def test_unrelated_test_files_get_demoted():
    """
    Test files that mention a domain token in symbols but not in the
    filename stem (e.g. test_retrieval_profile_service.py) should be
    demoted relative to source files.
    """
    engine = _make_engine_with_symbols({
        # A test file that mentions 'codecompass' in a symbol but not in the stem
        "tests/test_retrieval_profile_service.py": [
            "test_codecompass_profile_path_is_preserved",
            "test_profile_none_returns_none",
        ],
        # A source file that mentions 'codecompass' in the stem
        "agent/services/codecompass_retrieval_flag_service.py": [
            "CodeCompassFlagState",
            "as_dict",
        ],
    })

    results = engine.search("erkläre mir den codecompass", top_k=5)
    paths = [r.source for r in results]

    assert paths[0] == "agent/services/codecompass_retrieval_flag_service.py", (
        f"expected source file as top-1, got: {paths[0]}"
    )


def test_generic_query_unaffected():
    """
    A query with no clear domain token must not change behaviour: the
    selector gates on tokens of length ≥ 4 that are not stopwords.
    Generic 3-letter queries should not trigger the stem boost.
    """
    engine = _make_engine_with_symbols({
        "agent/services/xyz_service.py": ["build_xyz", "apply_xyz"],
        "tests/test_xyz_flow.py": ["test_xyz_happy_path", "test_xyz_error"],
    })

    # "xyz" is 3 chars — below the domain_stems threshold
    results = engine.search("explain xyz", top_k=5)
    paths = [r.source for r in results]

    # The original behaviour is preserved: ranking by path+symbol hits.
    # test_xyz_flow.py has 2 hits, xyz_service.py has 2 hits, but the
    # path 'xyz_service.py' has 'xyz' → +1.4 boost, so source still
    # wins because of the natural path-token bonus (no selector
    # interference).
    assert paths[0] == "agent/services/xyz_service.py", (
        f"source file should win on path-token bonus alone, got: {paths[0]}"
    )


def test_stopword_only_query_produces_no_results():
    """
    A query consisting only of stopwords (and tokens shorter than 3
    characters) must produce no candidates, not crash.
    """
    engine = _make_engine_with_symbols({
        "agent/services/codecompass_budgeting.py": ["resolve_codecompass_budget"],
    })
    results = engine.search("erkläre mir den", top_k=5)
    # "erkläre" has an umlaut; the regex catches it as one token.
    # "den" is a stopword, "mir" is a stopword, "erkläre" is the only
    # candidate token. None of the source-file symbols contain
    # "erkläre" → no matches → empty list.
    assert results == []


def test_test_file_with_domain_in_stem_keeps_natural_score():
    """
    A test file whose stem DOES contain the domain token (e.g.
    test_codecompass_*.py) keeps its natural symbol-based score — it
    is demoted only when it has no domain in the stem. This preserves
    callers' ability to discover which tests cover the queried area.
    """
    engine = _make_engine_with_symbols({
        "tests/test_codecompass_fts_engine.py": [
            "test_codecompass_fts_engine_returns_contextchunk_compatible_records",
            "test_codecompass_fts_engine_handles_empty_index",
        ],
        # Collateral test file — no domain in stem
        "tests/test_memory_tree_store_service.py": [
            "store",
            "ingestion",
            "test_codecompass_memory_handoff",  # mentions domain in a symbol
        ],
    })

    results = engine.search("erkläre mir den codecompass", top_k=5)
    paths = [r.source for r in results]

    # The test file WITH domain in stem should rank above the
    # collateral test file (which gets ×0.15 demotion).
    fts_idx = paths.index("tests/test_codecompass_fts_engine.py")
    memory_idx = paths.index("tests/test_memory_tree_store_service.py")
    assert fts_idx < memory_idx, (
        f"test_codecompass_fts_engine.py ({fts_idx}) should rank above "
        f"test_memory_tree_store_service.py ({memory_idx}): {paths}"
    )
