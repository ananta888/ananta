"""RCHCS-008: Tests for ReferencedContextHintStore."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.services.referenced_context_hint_schema import (
    GEN_DETERMINISTIC,
    GEN_MANUAL,
    KIND_FILE_SUMMARY,
    KIND_SYMBOL_HINT,
    KIND_TEST_HINT,
    STALENESS_FRESH,
    STALENESS_INVALID,
    STALENESS_STALE,
    GeneratorMetadata,
    HashMetadata,
    HintValidationError,
    ReferencedContextHint,
    SourceRef,
    make_hint_id,
)
from agent.services.referenced_context_hint_store import (
    ReferencedContextHintStore,
    get_referenced_context_hint_store,
    reset_referenced_context_hint_store,
)


def tmp_store() -> ReferencedContextHintStore:
    return ReferencedContextHintStore(tempfile.mkdtemp())


def make_hint(
    path: str = "agent/foo.py",
    kind: str = KIND_FILE_SUMMARY,
    summary: str = "Foo does bar.",
    sha256: str = "aabbcc",
) -> ReferencedContextHint:
    return ReferencedContextHint(
        id=make_hint_id(path, kind),
        kind=kind,
        title=f"Summary: {path}",
        summary=summary,
        source_refs=[SourceRef(path=path, sha256=sha256, role="primary_source")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
        hashes=HashMetadata(source_hash=sha256),
    )


# ── Put / get ─────────────────────────────────────────────────────────────────

def test_put_and_get_roundtrip():
    store = tmp_store()
    h = make_hint()
    store.put(h)
    retrieved = store.get(h.id)
    assert retrieved is not None
    assert retrieved.id == h.id
    assert retrieved.summary == h.summary


def test_put_writes_jsonl_file():
    store = tmp_store()
    h = make_hint()
    store.put(h)
    jpath = store._jsonl_path(KIND_FILE_SUMMARY)
    assert jpath.exists()
    lines = jpath.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["id"] == h.id


def test_get_returns_none_for_unknown_id():
    store = tmp_store()
    assert store.get("hint:file_summary:no:such") is None


def test_put_twice_latest_wins():
    store = tmp_store()
    h = make_hint(summary="first")
    store.put(h)
    h.summary = "second"
    store.put(h)
    retrieved = store.get(h.id)
    assert retrieved is not None
    assert retrieved.summary == "second"


def test_strict_mode_rejects_invalid_hint():
    store = tmp_store()
    h = make_hint()
    h.source_refs = []  # No refs for non-manual → invalid
    with pytest.raises(HintValidationError):
        store.put(h)


def test_non_strict_mode_stores_without_raise():
    store = ReferencedContextHintStore(tempfile.mkdtemp(), strict=False)
    h = make_hint()
    h.source_refs = []
    store.put(h)  # should not raise


# ── Search ────────────────────────────────────────────────────────────────────

def test_search_by_path():
    store = tmp_store()
    h1 = make_hint(path="agent/foo.py")
    h2 = make_hint(path="agent/bar.py", kind=KIND_SYMBOL_HINT,
                   summary="Bar is a helper.")
    store.put(h1)
    store.put(h2)
    results = store.search(path="foo.py")
    assert len(results) == 1
    assert results[0].source_refs[0].path == "agent/foo.py"


def test_search_by_kind():
    store = tmp_store()
    h1 = make_hint(path="agent/a.py", kind=KIND_FILE_SUMMARY, summary="A file.")
    h2 = make_hint(path="agent/b.py", kind=KIND_SYMBOL_HINT, summary="B symbol.")
    store.put(h1)
    store.put(h2)
    results = store.search(kind=KIND_FILE_SUMMARY)
    assert all(h.kind == KIND_FILE_SUMMARY for h in results)


def test_search_by_domain():
    store = tmp_store()
    h1 = make_hint(summary="This handles authentication flows.")
    h2 = make_hint(path="agent/other.py", summary="Unrelated utility.")
    store.put(h1)
    store.put(h2)
    results = store.search(domain="authentication")
    assert len(results) == 1
    assert "authentication" in results[0].summary.lower()


def test_search_excludes_invalid_by_default():
    store = tmp_store()
    h = make_hint()
    store.put(h)
    h.staleness_status = STALENESS_INVALID
    store.put(h)
    results = store.search()
    assert all(r.staleness_status != STALENESS_INVALID for r in results)


def test_search_results_sorted_by_confidence():
    store = tmp_store()
    h1 = make_hint(path="agent/a.py", summary="A module.")
    h1.confidence.score = 0.9
    h2 = make_hint(path="agent/b.py", summary="B module.")
    h2.confidence.score = 0.5
    store.put(h1)
    store.put(h2)
    results = store.search()
    scores = [r.confidence.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_limit():
    store = tmp_store()
    for i in range(5):
        h = make_hint(path=f"agent/f{i}.py", summary=f"File {i}.")
        store.put(h)
    results = store.search(limit=3)
    assert len(results) <= 3


# ── search_by_paths ───────────────────────────────────────────────────────────

def test_search_by_paths_returns_matching():
    store = tmp_store()
    h1 = make_hint(path="agent/foo.py", summary="Foo.")
    h2 = make_hint(path="agent/bar.py", summary="Bar.")
    store.put(h1)
    store.put(h2)
    results = store.search_by_paths(["agent/foo.py"])
    assert len(results) == 1
    assert results[0].source_refs[0].path == "agent/foo.py"


def test_search_by_paths_excludes_stale():
    store = tmp_store()
    h = make_hint()
    h.staleness_status = STALENESS_STALE
    store.put(h)
    results = store.search_by_paths(["agent/foo.py"])
    assert len(results) == 0


# ── Invalidation ──────────────────────────────────────────────────────────────

def test_invalidate_marks_hint_invalid():
    store = tmp_store()
    h = make_hint()
    store.put(h)
    result = store.invalidate(h.id)
    assert result is True
    retrieved = store.get(h.id)
    assert retrieved is not None
    assert retrieved.staleness_status == STALENESS_INVALID


def test_invalidate_returns_false_for_unknown():
    store = tmp_store()
    assert store.invalidate("hint:file_summary:no:such") is False


def test_invalidate_stale_for_paths(tmp_path):
    store = tmp_store()
    # Create a real temp file to hash
    src = tmp_path / "agent" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("# foo\n")
    src_hash = "deadbeef"  # old hash — mismatch will make it stale
    h = make_hint(path=str(src), sha256=src_hash)
    h.hashes.source_hash = src_hash
    store.put(h)
    changed = store.invalidate_stale_for_paths([str(src)])
    assert h.id in changed
    updated = store.get(h.id)
    assert updated is not None
    assert updated.staleness_status in (STALENESS_STALE, STALENESS_INVALID, "possibly_stale")


def test_invalidate_stale_no_effect_for_other_paths():
    store = tmp_store()
    h = make_hint(path="agent/foo.py")
    store.put(h)
    changed = store.invalidate_stale_for_paths(["agent/unrelated.py"])
    assert h.id not in changed


# ── Persistence ───────────────────────────────────────────────────────────────

def test_reload_from_disk():
    d = tempfile.mkdtemp()
    store1 = ReferencedContextHintStore(d)
    h = make_hint()
    store1.put(h)

    # Fresh store from same dir
    store2 = ReferencedContextHintStore(d)
    retrieved = store2.get(h.id)
    assert retrieved is not None
    assert retrieved.id == h.id
    assert retrieved.summary == h.summary


def test_manifest_written_on_put():
    store = tmp_store()
    h = make_hint()
    store.put(h)
    manifest = json.loads(store._manifest_path().read_text())
    assert h.id in manifest
    assert manifest[h.id]["kind"] == KIND_FILE_SUMMARY


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_empty():
    store = tmp_store()
    stats = store.stats()
    assert stats["total"] == 0


def test_stats_count_by_kind():
    store = tmp_store()
    store.put(make_hint(path="a.py", kind=KIND_FILE_SUMMARY, summary="A."))
    store.put(make_hint(path="b.py", kind=KIND_FILE_SUMMARY, summary="B."))
    store.put(make_hint(path="c.py", kind=KIND_SYMBOL_HINT, summary="C sym."))
    stats = store.stats()
    assert stats["total"] == 3
    assert stats["by_kind"].get(KIND_FILE_SUMMARY) == 2
    assert stats["by_kind"].get(KIND_SYMBOL_HINT) == 1


def test_count_by_kind():
    store = tmp_store()
    store.put(make_hint(path="a.py", summary="A."))
    store.put(make_hint(path="b.py", summary="B."))
    assert store.count(kind=KIND_FILE_SUMMARY) == 2


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_singleton_reset():
    reset_referenced_context_hint_store(None)
    svc1 = get_referenced_context_hint_store()
    svc2 = get_referenced_context_hint_store()
    assert svc1 is svc2
    reset_referenced_context_hint_store(None)


def test_singleton_custom():
    store = tmp_store()
    reset_referenced_context_hint_store(store)
    assert get_referenced_context_hint_store() is store
    reset_referenced_context_hint_store(None)
