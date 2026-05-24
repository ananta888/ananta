from __future__ import annotations

from agent.services.citation_verification_service import CitationVerificationService


def _base_answer(claims: list[dict]) -> dict:
    return {"schema": "grounded_answer.v1", "answer": "x", "claims": claims, "unsupported_notes": []}


def _catalog() -> dict:
    return {
        "schema": "source_catalog.v1",
        "catalog_id": "c1",
        "task_id": "t-1",
        "retrieval_trace_id": "rt-1",
        "retrieval_context_hash": "ctx-1",
        "retrieval_manifest_hash": "mh-1",
        "catalog_hash": "0123456789abcdef",
        "sources": [
            {
                "source_id": "SRC_0001",
                "source_type": "repo_file",
                "path": "src/a.py",
                "record_id": "r1",
                "line_start": 1,
                "line_end": 2,
                "content_hash": "aaaa1111",
                "manifest_hash": "mh-1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "created_at": 1.0,
                "task_id": "t-1",
            },
            {
                "source_id": "SRC_0002",
                "source_type": "repo_file",
                "path": "src/b.py",
                "record_id": "r2",
                "line_start": 1,
                "line_end": 2,
                "content_hash": "bbbb1111",
                "manifest_hash": "mh-1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": False,
                "created_at": 2.0,
                "task_id": "t-1",
            },
        ],
    }


def test_citation_verifier_verified_path() -> None:
    svc = CitationVerificationService()
    answer = _base_answer([
        {
            "claim_id": "CLM_0001",
            "text": "fact",
            "claim_type": "source_fact",
            "citation_refs": ["SRC_0001"],
            "confidence": "verified",
        }
    ])
    result = svc.verify(task_id="t-1", answer_payload=answer, source_catalog=_catalog())
    assert result["status"] == "verified"
    assert result["verified_claim_count"] == 1


def test_citation_verifier_unknown_source_fails() -> None:
    svc = CitationVerificationService()
    answer = _base_answer([
        {
            "claim_id": "CLM_0001",
            "text": "fact",
            "claim_type": "source_fact",
            "citation_refs": ["SRC_9999"],
            "confidence": "verified",
        }
    ])
    result = svc.verify(task_id="t-1", answer_payload=answer, source_catalog=_catalog())
    assert result["status"] == "failed_unknown_source"


def test_citation_verifier_policy_scope_fails() -> None:
    svc = CitationVerificationService()
    answer = _base_answer([
        {
            "claim_id": "CLM_0001",
            "text": "fact",
            "claim_type": "source_fact",
            "citation_refs": ["SRC_0002"],
            "confidence": "verified",
        }
    ])
    result = svc.verify(task_id="t-1", answer_payload=answer, source_catalog=_catalog())
    assert result["status"] == "failed_policy_scope"


def test_citation_verifier_tool_result_requires_run_reference() -> None:
    svc = CitationVerificationService()
    answer = _base_answer([
        {
            "claim_id": "CLM_0001",
            "text": "tool",
            "claim_type": "tool_result",
            "citation_refs": ["SRC_0001"],
            "confidence": "verified",
        }
    ])
    result = svc.verify(task_id="t-1", answer_payload=answer, source_catalog=_catalog())
    assert result["status"] == "failed_source_type_mismatch"


def test_citation_verifier_unverified_claim_counted() -> None:
    svc = CitationVerificationService()
    answer = _base_answer([
        {
            "claim_id": "CLM_0001",
            "text": "unsure",
            "claim_type": "uncertain",
            "citation_refs": [],
            "confidence": "unverified",
        }
    ])
    result = svc.verify(task_id="t-1", answer_payload=answer, source_catalog=_catalog())
    assert result["status"] == "verified"
    assert result["verified_claim_count"] == 0
    assert result["unverified_claim_count"] == 1
