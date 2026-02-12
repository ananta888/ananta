from pathlib import Path

from agent.hybrid_orchestrator import HybridOrchestrator


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
