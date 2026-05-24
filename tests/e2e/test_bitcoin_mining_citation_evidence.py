from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent.services.citation_verification_service import CitationVerificationService

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_bitcoin_mining_citation_evidence.py"
SUMMARY_PATH = REPO_ROOT / "ci-artifacts" / "bitcoin-mining-citation-evidence" / "evidence-summary.json"


def test_bitcoin_mining_citation_evidence_script_writes_verified_summary() -> None:
    subprocess.run([sys.executable, str(SCRIPT_PATH)], check=True, cwd=str(REPO_ROOT), capture_output=True, text=True)
    payload = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    assert payload["source_catalog_hash"]
    assert payload["run_id"] == "RUN_0001"
    assert payload["run_stdout_hash"]
    assert payload["citation_verification_status"] == "verified"


def test_bitcoin_mining_citation_evidence_rejects_uncited_or_invented_refs() -> None:
    svc = CitationVerificationService()
    source_catalog = {
        "schema": "source_catalog.v1",
        "catalog_id": "c1",
        "task_id": "btc-task-e2e",
        "retrieval_trace_id": "rt",
        "retrieval_context_hash": "ctx",
        "retrieval_manifest_hash": "mh",
        "catalog_hash": "abc",
        "sources": [
            {
                "source_id": "SRC_0001",
                "source_type": "repo_file",
                "path": "README.md",
                "record_id": "r1",
                "line_start": 1,
                "line_end": 2,
                "content_hash": "h1",
                "manifest_hash": "mh",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "created_at": 1.0,
                "task_id": "btc-task-e2e",
            }
        ],
    }

    uncited = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [{"claim_id": "CLM_0001", "text": "fact", "claim_type": "source_fact", "citation_refs": [], "confidence": "verified"}],
        "unsupported_notes": [],
    }
    invented = {
        "schema": "grounded_answer.v1",
        "answer": "x",
        "claims": [{"claim_id": "CLM_0002", "text": "fact", "claim_type": "source_fact", "citation_refs": ["SRC_9999"], "confidence": "verified"}],
        "unsupported_notes": [],
    }

    uncited_res = svc.verify(task_id="btc-task-e2e", answer_payload=uncited, source_catalog=source_catalog)
    invented_res = svc.verify(task_id="btc-task-e2e", answer_payload=invented, source_catalog=source_catalog)

    assert uncited_res["status"] == "failed_schema"
    assert invented_res["status"] == "failed_unknown_source"
