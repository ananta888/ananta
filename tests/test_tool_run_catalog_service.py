from __future__ import annotations

from agent.services.citation_verification_service import CitationVerificationService
from agent.services.tool_run_catalog_service import ToolRunCatalogService


def test_tool_run_catalog_builds_deterministic_entry() -> None:
    svc = ToolRunCatalogService()
    entry = svc.build_run_entry(
        task_id="t-1",
        index=1,
        tool_name="python",
        command="python demo.py",
        exit_code=0,
        stdout="ok",
        stderr="",
        artifact_paths=["out/result.json"],
        started_at=10.0,
        ended_at=11.0,
    )
    assert entry["source_id"] == "RUN_0001"
    assert entry["source_type"] == "tool_run"
    assert entry["task_id"] == "t-1"
    assert entry["stdout_hash"]


def test_citation_verifier_accepts_run_for_tool_result_claim() -> None:
    run_entry = ToolRunCatalogService().build_run_entry(
        task_id="t-1",
        index=1,
        tool_name="python",
        command="python demo.py",
        exit_code=0,
        stdout="nonce=123",
        stderr="",
    )
    answer = {
        "schema": "grounded_answer.v1",
        "answer": "tool output",
        "claims": [
            {
                "claim_id": "CLM_0001",
                "text": "result",
                "claim_type": "tool_result",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
            }
        ],
        "unsupported_notes": [],
    }
    source_catalog = {
        "schema": "source_catalog.v1",
        "catalog_id": "c1",
        "task_id": "t-1",
        "retrieval_trace_id": "rt-1",
        "retrieval_context_hash": "ctx-1",
        "retrieval_manifest_hash": "mh-1",
        "catalog_hash": "0123456789abcdef",
        "sources": [],
    }
    result = CitationVerificationService().verify(
        task_id="t-1",
        answer_payload=answer,
        source_catalog=source_catalog,
        tool_run_catalog=[run_entry],
    )
    assert result["status"] == "verified"
    assert result["verified_claim_count"] == 1
