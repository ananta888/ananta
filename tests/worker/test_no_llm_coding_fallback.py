from __future__ import annotations

from worker.coding.deterministic_fallback import build_no_llm_fallback_artifact


def test_no_llm_fallback_artifact_is_honest_and_structured() -> None:
    artifact = build_no_llm_fallback_artifact(
        task_id="T1",
        capability_id="worker.patch.propose",
        fallback_reason="no_model_provider",
        candidate_files=["src/a.py", "src/b.py"],
        constraints={"max_files": 2},
    )
    assert artifact["schema"] == "worker_no_llm_fallback.v1"
    assert artifact["llm_used"] is False
    assert artifact["fallback_reason"] == "no_model_provider"
    assert artifact["status"] == "degraded"
    assert artifact["candidate_files"] == ["src/a.py", "src/b.py"]
