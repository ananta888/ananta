#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.services.citation_verification_service import CitationVerificationService  # noqa: E402
from agent.services.source_catalog_service import SourceCatalogService  # noqa: E402
from agent.services.tool_run_catalog_service import ToolRunCatalogService  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "bitcoin_mining_demo"
OUT_DIR = REPO_ROOT / "ci-artifacts" / "bitcoin-mining-citation-evidence"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


def run() -> dict:
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")

    retrieval_payload = {
        "retrieval_trace": {
            "trace_id": "rt-btc-1",
            "context_hash": "ctx-btc-1",
            "manifest_hash": "mh-btc-1",
            "tenant_id": "bitcoin-mining-fixture",
            "scope": "repository",
        },
        "selected": [
            {
                "source_id": "SRC_0001",
                "source_version": "mh-btc-1",
                "tenant_id": "bitcoin-mining-fixture",
                "scope": "repository",
                "provenance": {
                    "fixture": "bitcoin_mining_demo",
                    "source_id": "SRC_0001",
                    "source_version": "mh-btc-1",
                },
                "path": "tests/fixtures/bitcoin_mining_demo/README.md",
                "record_id": "btc-readme-1",
                "line_start": 1,
                "line_end": 80,
                "content_hash": _sha256(readme),
                "manifest_hash": "mh-btc-1",
                "sensitivity": "internal",
                "allowed_for_llm_scope": True,
                "source_type": "repo_file",
            },
        ],
        "provenance": [],
    }
    source_catalog = SourceCatalogService().build_catalog(task_id="btc-task-e2e", retrieval_payload=retrieval_payload)
    source_ids_by_path = {
        str(source.get("path") or ""): str(source.get("source_id") or "")
        for source in source_catalog.get("sources", [])
    }
    readme_source_id = source_ids_by_path.get(
        "tests/fixtures/bitcoin_mining_demo/README.md",
        "",
    )
    if not readme_source_id:
        raise RuntimeError("bitcoin_mining_readme_source_unverified")

    proc = subprocess.run(
        [sys.executable, str(FIXTURE_DIR / "mining_demo.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(REPO_ROOT),
    )
    run_result = json.loads(proc.stdout)

    run_entry = ToolRunCatalogService().build_run_entry(
        task_id="btc-task-e2e",
        index=1,
        tool_name="python",
        command=f"{sys.executable} {FIXTURE_DIR / 'mining_demo.py'}",
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        artifact_paths=[str(FIXTURE_DIR / "expected_result.json")],
        started_at=1.0,
        ended_at=1.1,
        evidence_scope="test",
    )
    run_entry["result_payload"] = run_result

    answer = {
        "schema": "grounded_answer.v1",
        "answer": "Der Demo-Miner nutzt double_sha256 und findet unter Toy-Target eine gueltige Nonce.",
        "claims": [
            {
                "claim_id": "CLM_0001",
                "text": "Der Demo-Miner verwendet double_sha256 ueber einen vereinfachten Header.",
                "claim_type": "source_fact",
                "citation_refs": [readme_source_id],
                "confidence": "verified",
            },
            {
                "claim_id": "CLM_0002",
                "text": "Die konkrete gueltige Nonce und der Hash stammen aus dem Tool-Run.",
                "claim_type": "tool_result",
                "citation_refs": ["RUN_0001"],
                "confidence": "verified",
                "expected_evidence": {
                    "nonce": run_result["valid_result"]["nonce"],
                    "hash": run_result["valid_result"]["hash"],
                },
            },
        ],
        "unsupported_notes": [],
    }

    verification = CitationVerificationService().verify(
        task_id="btc-task-e2e",
        answer_payload=answer,
        source_catalog=source_catalog,
        tool_run_catalog=[run_entry],
    )

    summary = {
        "task_id": "btc-task-e2e",
        "source_catalog_id": source_catalog.get("catalog_id"),
        "source_catalog_hash": source_catalog.get("catalog_hash"),
        "run_id": run_entry.get("source_id"),
        "run_stdout_hash": run_entry.get("stdout_hash"),
        "citation_verification_status": verification.get("status"),
        "verified_claim_count": verification.get("verified_claim_count"),
        "unverified_claim_count": verification.get("unverified_claim_count"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "evidence-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
