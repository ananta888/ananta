from __future__ import annotations

from agent.services.citation_verification_service import CitationVerificationService


def _catalog(task_id: str = "t-neg", allowed: bool = True) -> dict:
    return {
        "schema": "source_catalog.v1",
        "catalog_id": "c-neg",
        "task_id": task_id,
        "retrieval_trace_id": "rt-neg",
        "retrieval_context_hash": "ctx-neg",
        "retrieval_manifest_hash": "mh-neg",
        "catalog_hash": "hash-neg",
        "sources": [
            {
                "source_id": "SRC_0001",
                "source_type": "repo_file",
                "path": "docs/fixture.md",
                "record_id": "r1",
                "line_start": 1,
                "line_end": 3,
                "content_hash": "h1",
                "manifest_hash": "mh-neg",
                "sensitivity": "internal",
                "allowed_for_llm_scope": allowed,
                "created_at": 1.0,
                "task_id": task_id,
            }
        ],
    }


def _answer(claim_type: str, refs: list[str]) -> dict:
    return {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [{"claim_id": "CLM_0001", "text": "x", "claim_type": claim_type, "citation_refs": refs, "confidence": "verified"}],
        "unsupported_notes": [],
    }


def test_negative_invented_file_path_maps_to_unknown_source() -> None:
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("source_fact", ["SRC_PATH_docs_fake_md"]), source_catalog=_catalog())
    assert res["status"] == "failed_unknown_source"


def test_negative_invented_src_id_is_rejected() -> None:
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("source_fact", ["SRC_9999"]), source_catalog=_catalog())
    assert res["status"] == "failed_unknown_source"


def test_negative_cross_task_source_is_rejected() -> None:
    catalog = _catalog(task_id="t-other")
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("source_fact", ["SRC_0001"]), source_catalog=catalog)
    assert res["status"] == "failed_cross_task_source"


def test_negative_excluded_source_is_rejected() -> None:
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("source_fact", ["SRC_0001"]), source_catalog=_catalog(allowed=False))
    assert res["status"] == "failed_policy_scope"


def test_negative_tool_result_without_run_is_rejected() -> None:
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("tool_result", ["SRC_0001"]), source_catalog=_catalog())
    assert res["status"] in {"failed_source_type_mismatch", "failed_missing_tool_run"}


def test_negative_hash_claim_with_readme_only_citation_is_rejected() -> None:
    res = CitationVerificationService().verify(task_id="t-neg", answer_payload=_answer("tool_result", ["SRC_0001"]), source_catalog=_catalog())
    assert res["status"] == "failed_source_type_mismatch"
