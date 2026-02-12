import time
from pathlib import Path

from agent.hybrid_orchestrator import HybridOrchestrator


def test_hybrid_orchestrator_handles_medium_load(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    code = tmp_path / "src"
    logs = tmp_path / "data"
    docs.mkdir()
    code.mkdir()
    logs.mkdir()

    for i in range(120):
        (code / f"mod_{i}.py").write_text(
            f"class Service{i}:\n"
            f"    def process_invoice_{i}(self):\n"
            "        return True\n",
            encoding="utf-8",
        )
    for i in range(80):
        (docs / f"doc_{i}.md").write_text(
            f"Invoice flow documentation section {i}. Timeout handling and retries.\n",
            encoding="utf-8",
        )
    for i in range(40):
        (logs / f"log_{i}.log").write_text(
            f"ERROR timeout invoice_id={i} retry exhausted\n",
            encoding="utf-8",
        )

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[docs, logs],
        max_context_chars=8000,
        max_context_tokens=2000,
    )

    start = time.perf_counter()
    result = orchestrator.get_relevant_context("find timeout retry bug for invoice flow")
    duration = time.perf_counter() - start

    assert result["chunks"]
    assert result["token_estimate"] <= 2000
    assert duration < 8.0
