"""CCRDS-017: unit tests for the DomainScopeFilter.

ContextChunk fixtures cover repo-relative, absolute and unclear sources
plus agentic command output; strict and non-strict variants. No
LlamaIndex/embedding dependency.
"""
from __future__ import annotations

from pathlib import Path

from agent.codecompass.domain_scope import ResolvedDomainScope
from agent.codecompass.domain_scope_filter import (
    DROP_REASON_AGENTIC_NO_SCOPED_LINES,
    DROP_REASON_OUT_OF_SCOPE,
    DROP_REASON_UNRESOLVABLE_SOURCE,
    build_scope_banner,
    filter_agentic_content,
    filter_chunks,
)
from agent.hybrid_orchestrator import ContextChunk


def _scope(paths: list[str], *, strict: bool = True) -> ResolvedDomainScope:
    return ResolvedDomainScope(
        active=True,
        strict=strict,
        selected_domain_ids=["rag-helper"],
        allowed_read_paths=paths,
        allowed_write_paths=paths,
    )


def _chunk(engine: str, source: str, content: str = "content") -> ContextChunk:
    return ContextChunk(engine=engine, source=source, content=content, score=1.0)


def test_inactive_scope_keeps_everything(tmp_path: Path) -> None:
    chunks = [_chunk("repository_map", "agent/x.py")]
    kept, stats = filter_chunks(chunks, ResolvedDomainScope(active=False), repo_root=tmp_path)
    assert len(kept) == 1
    assert stats.kept == 1 and stats.dropped == 0


def test_repo_relative_chunk_inside_scope_kept(tmp_path: Path) -> None:
    chunks = [_chunk("repository_map", "rag-helper/rag_helper/cli.py")]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert len(kept) == 1
    assert stats.kept == 1


def test_chunk_outside_scope_dropped(tmp_path: Path) -> None:
    chunks = [
        _chunk("repository_map", "rag-helper/rag_helper/cli.py"),
        _chunk("repository_map", "agent/config.py"),
    ]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert [c.source for c in kept] == ["rag-helper/rag_helper/cli.py"]
    assert stats.dropped_reasons == {DROP_REASON_OUT_OF_SCOPE: 1}


def test_absolute_path_under_repo_root_is_relativized(tmp_path: Path) -> None:
    absolute = tmp_path / "rag-helper" / "module.py"
    chunks = [_chunk("semantic_search", str(absolute))]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert len(kept) == 1
    assert stats.kept == 1


def test_absolute_path_outside_repo_root_dropped(tmp_path: Path) -> None:
    chunks = [_chunk("semantic_search", "/etc/passwd")]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert kept == []
    assert stats.dropped_reasons == {DROP_REASON_UNRESOLVABLE_SOURCE: 1}


def test_unclear_source_dropped_in_strict_mode(tmp_path: Path) -> None:
    chunks = [_chunk("semantic_search", "???:not a path")]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"], strict=True), repo_root=tmp_path)
    assert kept == []
    assert stats.dropped_reasons == {DROP_REASON_UNRESOLVABLE_SOURCE: 1}


def test_unclear_source_non_strict_dropped_with_warning(tmp_path: Path) -> None:
    chunks = [_chunk("semantic_search", "???:not a path")]
    kept, stats = filter_chunks(
        chunks, _scope(["rag-helper"], strict=False), repo_root=tmp_path
    )
    assert kept == []
    assert stats.warnings


def test_agentic_chunk_lines_filtered_to_scope(tmp_path: Path) -> None:
    content = (
        "rag-helper/rag_helper/cli.py:10:match in scope\n"
        "agent/config.py:5:match outside\n"
        "rag-helper/setup.py\n"
    )
    chunks = [_chunk("agentic_search", "rg -n query .", content)]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert len(kept) == 1
    assert "agent/config.py" not in kept[0].content
    assert "rag-helper/rag_helper/cli.py:10:match in scope" in kept[0].content
    assert kept[0].metadata.get("domain_scope_dropped_lines") == "1"
    assert stats.kept == 1


def test_agentic_chunk_without_scoped_lines_dropped(tmp_path: Path) -> None:
    chunks = [_chunk("agentic_search", "rg -n query .", "agent/a.py:1:x\nworker/b.py:2:y")]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert kept == []
    assert stats.dropped_reasons == {DROP_REASON_AGENTIC_NO_SCOPED_LINES: 1}


def test_filter_statistics_complete(tmp_path: Path) -> None:
    chunks = [
        _chunk("repository_map", "rag-helper/a.py"),
        _chunk("repository_map", "agent/b.py"),
        _chunk("semantic_search", "/outside/abs.md"),
    ]
    kept, stats = filter_chunks(chunks, _scope(["rag-helper"]), repo_root=tmp_path)
    assert stats.kept == 1
    assert stats.dropped == 2
    assert sum(stats.dropped_reasons.values()) == 2
    payload = stats.as_dict()
    assert set(payload) == {"kept", "dropped", "dropped_reasons", "warnings"}


def test_filter_agentic_content_line_parsing(tmp_path: Path) -> None:
    scope = _scope(["orders"])
    filtered, kept, dropped = filter_agentic_content(
        "orders/x.py:1:hit\nnot a path line at all\ncatalog/y.py:2:miss",
        scope,
        repo_root=tmp_path,
    )
    assert kept == 1 and dropped == 2
    assert filtered == "orders/x.py:1:hit"


def test_scope_banner_mentions_domains_paths_and_drops() -> None:
    scope = _scope(["rag-helper"])
    banner = build_scope_banner(scope)
    assert "DOMAIN-SCOPE AKTIV" in banner
    assert "rag-helper" in banner
