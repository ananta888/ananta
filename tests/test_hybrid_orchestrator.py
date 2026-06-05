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
