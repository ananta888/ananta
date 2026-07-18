from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts/test-gates/codecompass-e2e.json"
SCHEMA = ROOT / "schemas/testing/codecompass_e2e_gate.v1.json"
GENERATOR = ROOT / "scripts/generate_codecompass_e2e_gate.py"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_committed_codecompass_gate_is_schema_valid_and_fail_closed() -> None:
    report = _load(REPORT)
    jsonschema.Draft202012Validator(_load(SCHEMA)).validate(report)

    stages = {item["stage_id"] for item in report["pipeline"]["stages"]}
    assert stages == {
        "hub_task_persisted",
        "worker_ingestion_completed",
        "worker_artifacts_published",
        "hub_artifacts_materialized_atomically",
        "productive_search_port_queried",
        "hub_source_catalog_failed_closed_without_authority",
        "evidence_release_blocked_without_authority",
    }
    negatives = {item["gate_id"]: item["observed_reason_code"] for item in report["negative_gates"]}
    assert negatives == {
        "required_path_missing": "required_path_missing",
        "silently_skipped_nonzero": "silently_skipped_nonzero",
        "production_channel_empty": "production_channel_empty",
        "unverified_source_identity": "source_id_unverified",
        "stale_revision": "repository_revision_mismatch",
        "prompt_injection": "prompt_injection_detected",
    }

    encoded = REPORT.read_text(encoding="utf-8")
    assert not re.search(r'"/(?:home|tmp|workspace)/', encoded)
    assert "created_at" not in encoded
    assert "generated_at" not in encoded
    assert report["release_allowed"] is False
    assert report["source_grounding"] == {
        "authority": "unavailable",
        "status": "unverified",
        "provided_source_count": 0,
        "source_ids_synthesized": False,
        "grounded_claims_released": False,
        "fail_closed_reason_code": "source_catalog_not_current",
    }


def test_codecompass_gate_generator_is_byte_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        subprocess.run(
            [sys.executable, str(GENERATOR), "--output", str(output)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    assert first.read_bytes() == second.read_bytes() == REPORT.read_bytes()


def test_codecompass_gate_generator_check_accepts_committed_report() -> None:
    subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_positive_authority_mode_requires_explicit_external_identity(
    tmp_path: Path,
) -> None:
    output = tmp_path / "must-not-exist.json"
    environment = dict(os.environ)
    environment.pop("ANANTA_TEST_AUTHORIZED_SOURCE_ID", None)
    environment.pop("ANANTA_TEST_AUTHORIZED_SOURCE_IDS", None)

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--positive-authority",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "authorized_source_authority_required"
    assert not output.exists()


def test_positive_authority_mode_releases_only_externally_supplied_ids(
    tmp_path: Path,
) -> None:
    singular = str(os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID") or "").strip()
    plural = str(os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_IDS") or "")
    supplied = [singular] if singular else []
    supplied.extend(item.strip() for item in plural.split(",") if item.strip())
    if not supplied:
        pytest.skip("authoritative_source_evidence_unavailable")

    output = tmp_path / "positive.json"
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--positive-authority",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = _load(output)
    jsonschema.Draft202012Validator(_load(SCHEMA)).validate(report)

    assert report["release_allowed"] is True
    assert report["source_grounding"] == {
        "authority": "external_environment",
        "status": "verified",
        "provided_source_count": len(supplied),
        "source_ids_synthesized": False,
        "grounded_claims_released": True,
        "fail_closed_reason_code": None,
    }
    assert report["pipeline"]["counts"]["catalog_sources"] == len(supplied)
    assert report["pipeline"]["counts"]["released_evidence"] == len(supplied)
    positive_stages = {item["stage_id"] for item in report["pipeline"]["stages"]}
    assert "hub_source_catalog_authority_verified" in positive_stages
    assert "evidence_release_verified" in positive_stages
