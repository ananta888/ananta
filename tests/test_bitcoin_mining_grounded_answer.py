from __future__ import annotations

import json
from pathlib import Path

from agent.services.citation_verification_service import CitationVerificationService
from agent.services.tool_run_catalog_service import ToolRunCatalogService

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "bitcoin_mining_demo"


def _load_result() -> dict:
    return json.loads((_FIXTURE_DIR / "expected_result.json").read_text(encoding="utf-8"))


def _source_catalog(task_id: str) -> dict:
    return {
        "schema": "source_catalog.v1",
        "catalog_id": "btc-catalog-1",
        "task_id": task_id,
        "retrieval_trace_id": "rt-btc-1",
        "retrieval_context_hash": "ctx-btc-1",
        "retrieval_manifest_hash": "mh-btc-1",
        "catalog_hash": "hash-btc-catalog-1",
        "sources": [
            {
                "source_id": "SRC_0001",
                "source_type": "repo_file",
                "path": "tests/fixtures/bitcoin_mining_demo/README.md",
                "record_id": "readme-1",
                "line_start": 1,
                "line_end": 50,
                "content_hash": "readmehash1",
                "manifest_hash": "mh-btc-1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "created_at": 1.0,
                "task_id": task_id,
            }
        ],
    }


def _run_entry(task_id: str) -> dict:
    result_payload = _load_result()
    run = ToolRunCatalogService().build_run_entry(
        task_id=task_id,
        index=1,
        tool_name="python",
        command="python3 tests/fixtures/bitcoin_mining_demo/mining_demo.py",
        exit_code=0,
        stdout=json.dumps(result_payload, sort_keys=True),
        stderr="",
        artifact_paths=["tests/fixtures/bitcoin_mining_demo/expected_result.json"],
        started_at=1.0,
        ended_at=1.5,
    )
    run["result_payload"] = result_payload
    return run


def test_bitcoin_grounded_answer_passes_with_src_and_run_evidence() -> None:
    task_id = "btc-task-1"
    result_payload = _load_result()
    run_entry = _run_entry(task_id)
    answer = {
        "schema": "grounded_answer.v1",
        "answer": "Der Demo-Miner nutzt double_sha256 und findet mit Toy-Target eine gueltige Nonce.",
        "claims": [
            {
                "claim_id": "CLM_0001",
                "text": "Der Demo-Miner verwendet double_sha256 ueber den Header.",
                "claim_type": "source_fact",
                "citation_refs": ["SRC_0001"],
                "confidence": "verified",
            },
            {
                "claim_id": "CLM_0002",
                "text": "Nonce und Hash stammen aus dem Tool-Run.",
                "claim_type": "tool_result",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
                "expected_evidence": {
                    "nonce": result_payload["valid_result"]["nonce"],
                    "hash": result_payload["valid_result"]["hash"],
                },
            },
        ],
        "unsupported_notes": [],
    }

    res = CitationVerificationService().verify(
        task_id=task_id,
        answer_payload=answer,
        source_catalog=_source_catalog(task_id),
        tool_run_catalog=[run_entry],
    )

    assert res["status"] == "verified"
    assert res["verified_claim_count"] == 2


def test_bitcoin_claim_type_mismatch_is_rejected_deterministically() -> None:
    task_id = "btc-task-1"
    run_entry = _run_entry(task_id)

    wrong_src_claim = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [
            {
                "claim_id": "CLM_0001",
                "text": "Algo claim mit falscher Citation.",
                "claim_type": "source_fact",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
            }
        ],
        "unsupported_notes": [],
    }
    wrong_run_claim = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [
            {
                "claim_id": "CLM_0002",
                "text": "Tool-Ergebnis claim mit falscher Citation.",
                "claim_type": "tool_result",
                "citation_refs": ["SRC_0001"],
                "confidence": "verified",
            }
        ],
        "unsupported_notes": [],
    }

    svc = CitationVerificationService()
    res_src = svc.verify(task_id=task_id, answer_payload=wrong_src_claim, source_catalog=_source_catalog(task_id), tool_run_catalog=[run_entry])
    res_run = svc.verify(task_id=task_id, answer_payload=wrong_run_claim, source_catalog=_source_catalog(task_id), tool_run_catalog=[run_entry])

    assert res_src["status"] == "failed_source_type_mismatch"
    assert res_run["status"] == "failed_source_type_mismatch"


def test_bitcoin_tool_result_requires_existing_run_and_matching_hash_nonce() -> None:
    task_id = "btc-task-1"
    result_payload = _load_result()
    run_entry = _run_entry(task_id)

    missing_run_answer = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [
            {
                "claim_id": "CLM_0002",
                "text": "Tool claim ohne vorhandenen run.",
                "claim_type": "tool_result",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
            }
        ],
        "unsupported_notes": [],
    }
    mismatch_answer = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [
            {
                "claim_id": "CLM_0003",
                "text": "Falscher Hash/Nonce claim.",
                "claim_type": "tool_result",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
                "expected_evidence": {
                    "nonce": result_payload["valid_result"]["nonce"] + 1,
                    "hash": result_payload["valid_result"]["hash"],
                },
            }
        ],
        "unsupported_notes": [],
    }

    svc = CitationVerificationService()
    res_missing = svc.verify(task_id=task_id, answer_payload=missing_run_answer, source_catalog=_source_catalog(task_id), tool_run_catalog=[])
    res_mismatch = svc.verify(task_id=task_id, answer_payload=mismatch_answer, source_catalog=_source_catalog(task_id), tool_run_catalog=[run_entry])

    assert res_missing["status"] == "failed_unknown_source"
    assert res_mismatch["status"] == "failed_tool_evidence_mismatch"
