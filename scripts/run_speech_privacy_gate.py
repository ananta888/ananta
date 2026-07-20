#!/usr/bin/env python3
"""Execute local speech privacy lifecycle and content-leak gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)
from agent.services.speech_privacy_lifecycle_service import SPEECH_DATA_PHASES
from agent.services.speech_privacy_production_composition import (
    PRODUCTION_SPEECH_PRIVACY_PHASES,
    assert_speech_privacy_production_composition,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "agent/db_models/__init__.py",
    "agent/db_models/ml_intern_training.py",
    "agent/db_models/speech_evidence.py",
    "agent/db_models/speech_evidence_sync.py",
    "agent/db_models/speech_reconciliation.py",
    "agent/repositories/ml_intern_speech_adapter_registry.py",
    "agent/repositories/ml_intern_training.py",
    "agent/repositories/semantic_media_audit_repository.py",
    "agent/repositories/speech_consent_repository.py",
    "agent/repositories/speech_evidence.py",
    "agent/repositories/speech_evidence_lineage.py",
    "agent/repositories/speech_evidence_sync.py",
    "agent/repositories/speech_reconciliation.py",
    "agent/services/ml_intern_speech_adapter_export.py",
    "agent/services/ml_intern_speech_adapter_registry.py",
    "agent/services/ml_intern_speech_lineage_service.py",
    "agent/services/ml_intern_speech_revocation_service.py",
    "agent/services/semantic_media_canary_scan_service.py",
    "agent/services/speech_evidence_consent_service.py",
    "agent/services/speech_evidence_key_service.py",
    "agent/services/speech_evidence_peer_revocation_service.py",
    "agent/services/speech_evidence_retention_cleanup_service.py",
    "agent/services/speech_evidence_revocation_service.py",
    "agent/services/speech_evidence_store_service.py",
    "agent/services/speech_privacy_lifecycle_service.py",
    "agent/services/speech_privacy_production_composition.py",
    "migrations/versions/fe3f4a5b6c7d_add_speech_privacy_lifecycle.py",
    "docs/security/acoustic-residual-privacy.md",
    "scripts/benchmark/acoustic_residual_privacy.py",
    "voice_runtime/features/residual.py",
    "tests/e2e/test_speech_evidence_revocation_flow.py",
    "tests/e2e/test_speech_privacy_persistent_api.py",
    "tests/security/test_semantic_media_content_leaks.py",
    "tests/security/test_semantic_media_persistent_canary_scan.py",
    "tests/security/test_speech_evidence_key_lifecycle.py",
    "tests/security/test_acoustic_residual_privacy.py",
    "tests/test_acoustic_residual_privacy_gate.py",
    "tests/test_speech_evidence_peer_revocation.py",
    "tests/test_speech_evidence_retention_cleanup.py",
    "tests/test_speech_evidence_revocation.py",
    "tests/test_speech_evidence_atomic_races.py",
    "tests/test_speech_evidence_sync_production.py",
    "tests/test_speech_reconciliation_production_composition.py",
    "tests/test_speech_reconciliation_recovery.py",
    "tests/test_ml_intern_speech_dataset_manifest.py",
    "tests/test_ml_intern_speech_adapter_registry.py",
    "tests/test_speech_adapter_export_production.py",
    "tests/test_speech_adapter_inference.py",
    "tests/test_voice_privacy_complete_deletion.py",
    "tests/test_speech_privacy_gate.py",
    "tests/speech_evidence_support.py",
    "frontend-angular/src/app/features/voice/voice-long-run-spool.ts",
    "frontend-angular/src/app/features/voice/voice-long-run-spool.spec.ts",
    "scripts/run_speech_privacy_gate.py",
)
PYTHON_SUITES = (
    "tests/e2e/test_speech_evidence_revocation_flow.py",
    "tests/e2e/test_speech_privacy_persistent_api.py",
    "tests/security/test_semantic_media_content_leaks.py",
    "tests/security/test_semantic_media_persistent_canary_scan.py",
    "tests/security/test_speech_evidence_key_lifecycle.py",
    "tests/security/test_acoustic_residual_privacy.py",
    "tests/test_acoustic_residual_privacy_gate.py",
    "tests/test_speech_evidence_peer_revocation.py",
    "tests/test_speech_evidence_revocation.py",
    "tests/test_speech_evidence_retention_cleanup.py",
    "tests/test_speech_evidence_atomic_races.py",
    "tests/test_speech_evidence_sync_production.py",
    "tests/test_speech_reconciliation_production_composition.py",
    "tests/test_speech_reconciliation_recovery.py",
    "tests/test_ml_intern_speech_dataset_manifest.py",
    "tests/test_ml_intern_speech_adapter_registry.py",
    "tests/test_speech_adapter_export_production.py",
    "tests/test_speech_adapter_inference.py",
    "tests/test_voice_privacy_complete_deletion.py",
)
BROWSER_SUITES = ("src/app/features/voice/voice-long-run-spool.spec.ts",)
CONFIG = {
    "phase_count": 11,
    "phases": sorted(SPEECH_DATA_PHASES),
    "channels": ["log", "db", "audit", "task", "artifact", "metric", "browserstore"],
    "composition": "hub-sql-speech-privacy-v1",
    "python_suites": list(PYTHON_SUITES),
    "browser_suites": list(BROWSER_SUITES),
    "remote_ack_policy": "acknowledged-or-bounded-unresolved",
}


def run(*, execute: bool) -> GateEvidence:
    source_digest = source_hash(ROOT, SOURCES)
    config_digest = canonical_sha256(CONFIG)
    if not execute:
        return unavailable_evidence(
            "ASMP-QA-008",
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="privacy_execution_not_requested",
        )
    reasons = _composition_reason_codes()
    python_exit_code, python_reason = _run_suite(
        [sys.executable, "-m", "pytest", "-q", *PYTHON_SUITES],
        cwd=ROOT,
        timeout=900,
    )
    browser_exit_code, browser_reason = _run_suite(
        ["npx", "vitest", "run", *BROWSER_SUITES],
        cwd=ROOT / "frontend-angular",
        timeout=180,
    )
    if python_reason is not None:
        reasons.append(f"speech_privacy_persistent_suite_{python_reason}")
    elif python_exit_code != 0:
        reasons.append("speech_privacy_persistent_suite_failed")
    if browser_reason is not None:
        reasons.append(f"speech_privacy_browser_store_suite_{browser_reason}")
    elif browser_exit_code != 0:
        reasons.append("speech_privacy_browser_store_suite_failed")
    reasons = sorted(set(reasons))
    status = "passed" if not reasons else "failed"
    return GateEvidence(
        gate_id="ASMP-QA-008",
        status=status,
        reason_codes=tuple(reasons),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements={
            "phase_count": len(SPEECH_DATA_PHASES),
            "channel_count": len(CONFIG["channels"]),
            "python_suite_count": len(PYTHON_SUITES),
            "browser_suite_count": len(BROWSER_SUITES),
            "python_exit_code": python_exit_code,
            "browser_exit_code": browser_exit_code,
        },
    )


def _composition_reason_codes() -> list[str]:
    reasons: list[str] = []
    try:
        assert_speech_privacy_production_composition()
    except Exception:
        reasons.append("speech_privacy_product_composition_missing")
    if PRODUCTION_SPEECH_PRIVACY_PHASES != SPEECH_DATA_PHASES or len(SPEECH_DATA_PHASES) != 11:
        reasons.append("speech_privacy_phase_contract_mismatch")
    lifecycle_e2e = (ROOT / "tests/e2e/test_speech_evidence_revocation_flow.py").read_text(encoding="utf-8")
    if (
        "build_speech_privacy_lifecycle_service" not in lifecycle_e2e
        or "InMemorySpeechPrivacyTombstoneRepository" in lifecycle_e2e
        or "class _Fences" in lifecycle_e2e
        or "class _Keys" in lifecycle_e2e
    ):
        reasons.append("speech_privacy_e2e_product_composition_missing")
    return sorted(set(reasons))


def _run_suite(command: list[str], *, cwd: Path, timeout: int) -> tuple[int, str | None]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
        return int(completed.returncode), None
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError:
        return 127, "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = run(execute=args.execute)
    if args.output:
        write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
