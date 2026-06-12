from pathlib import Path

from agent import hybrid_orchestrator
from agent.config import settings
from agent.hybrid_orchestrator import HybridOrchestrator, RepositoryMapEngine
from agent.hybrid_repository_scan import tracked_code_files


def test_get_relevant_context_returns_mixed_chunks(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "module.py").write_text(
        "class PaymentService:\n    def process_invoice(self):\n        return True\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "README.md").write_text(
        "Invoice pipeline documentation and troubleshooting notes.",
        encoding="utf-8",
    )
    (tmp_path / "data" / "app.log").write_text(
        "ERROR invoice_id=42 failed due to timeout",
        encoding="utf-8",
    )

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[tmp_path / "docs", tmp_path / "data"],
        max_context_chars=2000,
    )

    result = orchestrator.get_relevant_context("Find invoice timeout bug in module.py and docs")

    assert "chunks" in result
    assert result["chunks"]
    assert len(result["context_text"]) <= 2000
    assert any(chunk["engine"] == "repository_map" for chunk in result["chunks"])


def test_redaction_applies_to_sensitive_patterns(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text(
        "token=sk-1234567890ABCDE and user@example.com",
        encoding="utf-8",
    )
    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[tmp_path / "docs"],
        max_context_chars=1000,
        max_context_tokens=500,
    )
    result = orchestrator.get_relevant_context("show token and email from docs")
    assert "[REDACTED]" in result["context_text"]
    assert "user@example.com" not in result["context_text"]


def test_tree_sitter_language_support_matrix_contains_expected_entries() -> None:
    matrix = RepositoryMapEngine.language_support_matrix()
    assert ".py" in matrix
    assert matrix[".py"]["tree_sitter_language"] == "python"
    assert ".rb" in matrix
    assert matrix[".rb"]["fallback"] == "regex"


def test_parser_resolution_falls_back_for_unsupported_extension(tmp_path: Path) -> None:
    engine = RepositoryMapEngine(repo_root=tmp_path)
    unsupported = tmp_path / "note.txt"
    unsupported.write_text("hello", encoding="utf-8")
    assert engine._parser_for_file(unsupported) is None


def test_context_manager_route_uses_configured_rag_quotas(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_code_repo", 5, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_code_semantic", 3, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_default_repo", 2, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_default_semantic", 7, raising=False)

    manager = hybrid_orchestrator.ContextManager()

    assert manager.route("python service bug")["repository_map"] == 5
    assert manager.route("python service bug")["semantic_search"] == 3
    assert manager.route("plain request") == {
        "repository_map": 2,
        "semantic_search": 7,
        "agentic_search": 1,
    }


def test_tracked_code_files_uses_configured_scan_exclusions(tmp_path: Path, monkeypatch) -> None:
    keep = tmp_path / "keep"
    skip = tmp_path / "custom-skip"
    keep.mkdir()
    skip.mkdir()
    (keep / "included.py").write_text("def included():\n    return True\n", encoding="utf-8")
    (skip / "excluded.py").write_text("def excluded():\n    return True\n", encoding="utf-8")
    monkeypatch.setattr(settings, "rag_scan_exclude_dirs", " .git, custom-skip , node_modules ", raising=False)

    files = tracked_code_files(repo_root=tmp_path, code_extensions={".py"}, max_files=20)
    rel = {path.relative_to(tmp_path).as_posix() for path in files}

    assert "keep/included.py" in rel
    assert "custom-skip/excluded.py" not in rel


def test_semantic_search_defaults_to_non_embedding_fallback(tmp_path: Path, monkeypatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("Local fallback retrieval content.", encoding="utf-8")

    class _ShouldNotRun:
        @staticmethod
        def from_documents(*_args, **_kwargs):
            raise AssertionError("VectorStoreIndex.from_documents should not run by default")

    monkeypatch.delenv("ANANTA_ENABLE_LLAMAINDEX_EMBEDDINGS", raising=False)
    monkeypatch.setattr(hybrid_orchestrator, "VectorStoreIndex", _ShouldNotRun)
    monkeypatch.setattr(hybrid_orchestrator, "StorageContext", object())
    monkeypatch.setattr(hybrid_orchestrator, "load_index_from_storage", object())
    monkeypatch.setattr(hybrid_orchestrator, "SimpleDirectoryReader", object())

    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[docs], max_context_chars=1000)
    result = orchestrator.get_relevant_context("fallback retrieval")
    assert result["chunks"]


def test_context_manager_route_uses_docs_quota_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_docs_semantic", 8, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_docs_repo", 3, raising=False)
    manager = hybrid_orchestrator.ContextManager()
    quotas = manager.route("pdf documentation readme")
    assert quotas["semantic_search"] == 8
    assert quotas["repository_map"] == 3


def test_context_manager_route_uses_fs_quota_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_fs_agentic", 5, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_fs_repo", 4, raising=False)
    manager = hybrid_orchestrator.ContextManager()
    quotas = manager.route("find datei folder suche")
    assert quotas["agentic_search"] == 5
    assert quotas["repository_map"] == 4


def test_get_relevant_context_without_scope_unchanged(tmp_path: Path) -> None:
    """CCRDS-018: ohne Scope identisches Verhalten, kein domain_scope-Block."""
    (tmp_path / "module.py").write_text("def process_order(): pass\n", encoding="utf-8")
    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[], max_context_chars=2000)
    baseline = orchestrator.get_relevant_context("process order module")
    again = orchestrator.get_relevant_context("process order module", domain_scope=None)
    assert "domain_scope" not in baseline
    assert "error" not in baseline
    assert [c["source"] for c in baseline["chunks"]] == [c["source"] for c in again["chunks"]]


def test_get_relevant_context_with_scope_filters_and_banners(tmp_path: Path) -> None:
    from agent.codecompass.domain_scope import ResolvedDomainScope

    (tmp_path / "orders").mkdir()
    (tmp_path / "catalog").mkdir()
    (tmp_path / "orders" / "service.py").write_text(
        "class OrderInvoiceService:\n    def create_invoice(self):\n        return True\n",
        encoding="utf-8",
    )
    (tmp_path / "catalog" / "service.py").write_text(
        "class CatalogInvoiceService:\n    def create_invoice(self):\n        return True\n",
        encoding="utf-8",
    )
    scope = ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["orders"],
        allowed_read_paths=["orders"],
        allowed_write_paths=["orders"],
    )
    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[], max_context_chars=4000)
    result = orchestrator.get_relevant_context("invoice service class", domain_scope=scope)
    sources = [c["source"] for c in result["chunks"]]
    assert all("catalog" not in s for s in sources)
    assert result["domain_scope"]["active_domain_ids"] == ["orders"]
    assert "DOMAIN-SCOPE AKTIV" in result["context_text"]


def test_get_relevant_context_strict_violation_fails_closed(tmp_path: Path) -> None:
    from agent.codecompass.domain_scope import (
        DomainScopeViolation,
        ResolvedDomainScope,
        VIOLATION_UNKNOWN_DOMAIN,
    )

    (tmp_path / "module.py").write_text("def f(): pass\n", encoding="utf-8")
    scope = ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["nope"],
        violations=[DomainScopeViolation(kind=VIOLATION_UNKNOWN_DOMAIN, message="unknown")],
    )
    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[], max_context_chars=2000)
    result = orchestrator.get_relevant_context("anything", domain_scope=scope)
    assert result["error"] == "domain_scope_violation"
    assert result["chunks"] == []
    assert result["context_text"] == ""


def test_run_with_sgpt_strict_violation_skips_llm(tmp_path: Path, monkeypatch) -> None:
    from agent.codecompass.domain_scope import (
        DomainScopeViolation,
        ResolvedDomainScope,
        VIOLATION_UNKNOWN_DOMAIN,
    )

    def _no_llm(**_kwargs):
        raise AssertionError("LLM must not be called on strict scope violation")

    monkeypatch.setattr(hybrid_orchestrator, "run_llm_cli_command", _no_llm)
    scope = ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["nope"],
        violations=[DomainScopeViolation(kind=VIOLATION_UNKNOWN_DOMAIN, message="unknown")],
    )
    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[], max_context_chars=2000)
    result = orchestrator.run_with_sgpt("anything", domain_scope=scope)
    assert result["returncode"] == 1
    assert result["errors"] == "domain_scope_violation"


def test_repository_map_engine_scope_limits_candidates(tmp_path: Path) -> None:
    (tmp_path / "orders").mkdir()
    (tmp_path / "catalog").mkdir()
    (tmp_path / "orders" / "invoice.py").write_text("def invoice(): pass\n", encoding="utf-8")
    (tmp_path / "catalog" / "invoice.py").write_text("def invoice(): pass\n", encoding="utf-8")
    engine = RepositoryMapEngine(repo_root=tmp_path)
    scoped = engine.search("invoice", allowed_paths=["orders"])
    assert scoped and all(c.source.startswith("orders/") for c in scoped)
    unscoped = engine.search("invoice")
    assert {c.source for c in unscoped} >= {c.source for c in scoped}


def test_agentic_engine_scope_rewrites_rg_and_blocks_empty(tmp_path: Path) -> None:
    from agent.hybrid_orchestrator import AgenticSearchEngine

    engine = AgenticSearchEngine(repo_root=tmp_path)
    # text_grep: trailing "." is replaced by the scoped paths.
    args = engine.skills[-1].build_command("find invoice")
    scoped = engine._apply_scope(args, ["orders", "billing"])
    assert scoped is not None
    assert scoped[-2:] == ["billing", "orders"]
    assert "." not in scoped
    # cat outside scope is dropped entirely.
    assert engine._apply_scope(["cat", "catalog/x.py"], ["orders"]) is None
    assert engine._apply_scope(["cat", "orders/x.py"], ["orders"]) == ["cat", "orders/x.py"]
    # Active scope without allowed paths: no agentic search at all.
    assert engine.search("find invoice", allowed_paths=[]) == []


def test_agentic_engine_query_path_injection_ineffective(tmp_path: Path) -> None:
    from agent.hybrid_orchestrator import AgenticSearchEngine

    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "a.py").write_text("invoice_marker = 1\n", encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "b.py").write_text("invoice_marker = 2\n", encoding="utf-8")
    engine = AgenticSearchEngine(repo_root=tmp_path)
    # The query tries to smuggle a path; it stays a single rg pattern arg.
    chunks = engine.search("invoice_marker ../secret secret/b.py", allowed_paths=["orders"])
    for chunk in chunks:
        assert "secret/b.py" not in chunk.content


def test_get_relevant_context_uses_normalizer(tmp_path: Path, monkeypatch) -> None:
    """Query normalization is called and original query is always in the retrieval set."""
    from agent import rag_query_normalizer

    calls: list[str] = []
    original_fn = rag_query_normalizer.normalize_query_from_settings

    def _tracking(q: str) -> list[str]:
        calls.append(q)
        return original_fn(q)

    monkeypatch.setattr(rag_query_normalizer, "normalize_query_from_settings", _tracking)
    monkeypatch.setattr(
        hybrid_orchestrator, "normalize_query_from_settings", _tracking
    )

    (tmp_path / "module.py").write_text("def process_task(): pass\n", encoding="utf-8")
    orchestrator = HybridOrchestrator(repo_root=tmp_path, data_roots=[], max_context_chars=500)
    orchestrator.get_relevant_context("Wie funktioniert der service?")
    assert calls, "normalize_query_from_settings was never called"
    assert calls[0] == "Wie funktioniert der service?"
