from pathlib import Path
import subprocess

from agent.hybrid_orchestrator import HybridOrchestrator


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)


def test_hybrid_orchestrator_returns_symbol_context(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        """
class Demo:
    pass

def calculate_sum(a, b):
    return a + b
""".strip(),
        encoding="utf-8",
    )
    _init_git_repo(tmp_path)

    orchestrator = HybridOrchestrator(repo_path=tmp_path)
    result = orchestrator.get_relevant_context("python function calculate")

    assert "symbol_map" in result
    assert any(entry["symbol"] == "calculate_sum" for entry in result["symbol_map"])


def test_hybrid_orchestrator_uses_agentic_search_when_low_confidence(tmp_path: Path) -> None:
    (tmp_path / "README.txt").write_text("nur text", encoding="utf-8")
    _init_git_repo(tmp_path)

    orchestrator = HybridOrchestrator(repo_path=tmp_path)
    result = orchestrator.get_relevant_context("completely unknown token")

    assert "agentic_findings" in result
    assert isinstance(result["agentic_findings"], list)
