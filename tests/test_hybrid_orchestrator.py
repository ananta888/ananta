from pathlib import Path

from agent.hybrid_orchestrator import HybridOrchestrator, RepositoryMapEngine


def test_get_relevant_context_returns_mixed_chunks(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "module.py").write_text(
        "class PaymentService:\n"
        "    def process_invoice(self):\n"
        "        return True\n",
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
