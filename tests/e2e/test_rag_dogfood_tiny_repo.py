from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "tiny_project"


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_rag_dogfood_tiny_repo_returns_relevant_bounded_sources(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_rag_tiny_repo(
        FIXTURE_ROOT,
        query="How do we check docker service health?",
        run_id="rag-tiny-001",
    )

    assert result.flow_entry["status"] == "passed"
    assert result.flow_entry["blocking"] is True
    assert result.artifact_refs

    retrieval_report = json.loads(_resolve_ref(result.artifact_refs[0]).read_text(encoding="utf-8"))
    results = list(retrieval_report.get("results") or [])
    assert results
    assert any(item["source_path"] == "src/docker_ops.py" for item in results)

    for item in results:
        source = item["source_path"]
        assert not source.startswith("..")
        assert (FIXTURE_ROOT / source).exists()
        assert "matched_tokens" in item["reason"]

    assert retrieval_report["bounded_to_fixture_root"] is True
