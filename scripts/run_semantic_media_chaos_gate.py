#!/usr/bin/env python3
"""Execute persistent Hub/API/worker lifecycle failure scenarios."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)

try:
    from scripts.e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIVE_FAILOVER = ROOT / "artifacts/domain/semantic-sfu-live-failover.json"
SOURCES = (
    "agent/repositories/semantic_relay_shared_store.py",
    "agent/repositories/semantic_sfu_admission_repository.py",
    "agent/repositories/speech_reconciliation.py",
    "agent/repositories/speech_adaptation.py",
    "agent/services/media_topology_policy.py",
    "agent/services/background/speech_adaptation_dispatcher.py",
    "agent/services/background/speech_reconciliation_reconciler.py",
    "agent/services/semantic_relay_service.py",
    "agent/services/semantic_sfu_admission_service.py",
    "agent/services/semantic_sfu_group_key_service.py",
    "agent/services/speech_adaptation_job_service.py",
    "agent/services/speech_reconciliation_repository_adapters.py",
    "agent/services/speech_reconciliation_result_admission_service.py",
    "tests/chaos/test_semantic_sfu_failover.py",
    "tests/integration/test_semantic_relay_multi_hub.py",
    "tests/security/test_semantic_relay_api.py",
    "tests/test_semantic_sfu_admission_repository.py",
    "tests/test_semantic_sfu_group_keys.py",
    "tests/test_speech_reconciliation_repository_adapters.py",
    "tests/test_speech_reconciliation_recovery.py",
    "tests/test_speech_reconciliation_api.py",
    "tests/test_speech_adaptation_dispatcher.py",
    "tests/test_speech_adaptation_worker_port.py",
    "frontend-angular/src/app/services/pair-view-sync.service.ts",
    "frontend-angular/src/app/services/pair-view-sync.reconnect.spec.ts",
    "frontend-angular/src/app/services/livekit-sfu-transport.service.ts",
    "frontend-angular/src/app/services/livekit-sfu-transport.service.spec.ts",
    "frontend-angular/src/app/services/semantic-recovery.service.ts",
    "frontend-angular/src/app/services/semantic-recovery.service.spec.ts",
    "frontend-angular/src/app/services/speech-evidence-datachannel-transport.service.ts",
    "frontend-angular/src/app/services/speech-evidence-datachannel-transport.service.spec.ts",
    "frontend-angular/src/app/services/speech-reconciliation-api.service.ts",
    "frontend-angular/src/app/services/speech-reconciliation-api.service.spec.ts",
    "docs/operations/semantic-media-sfu.md",
    "scripts/run_semantic_media_chaos_gate.py",
    "scripts/e2e/semantic_sfu_failover_e2e.py",
    "scripts/e2e/semantic_sfu_hub_e2e.py",
    "scripts/spikes/semantic_sfu_failover.mjs",
)
PYTHON_SCENARIOS = (
    "tests/chaos/test_semantic_sfu_failover.py",
    "tests/integration/test_semantic_relay_multi_hub.py",
    "tests/security/test_semantic_relay_api.py",
    "tests/test_semantic_sfu_admission_repository.py",
    "tests/test_semantic_sfu_group_keys.py::test_concurrent_hub_is_fenced_then_failover_rekeys_and_hides_stale_packages",
    "tests/test_speech_reconciliation_repository_adapters.py",
    "tests/test_speech_reconciliation_recovery.py",
    "tests/test_speech_reconciliation_api.py",
    "tests/test_speech_adaptation_dispatcher.py",
    "tests/test_speech_adaptation_worker_port.py",
)
BROWSER_SCENARIOS = (
    "src/app/services/pair-view-sync.reconnect.spec.ts",
    "src/app/services/livekit-sfu-transport.service.spec.ts",
    "src/app/services/semantic-recovery.service.spec.ts",
    "src/app/services/speech-evidence-datachannel-transport.service.spec.ts",
    "src/app/services/speech-reconciliation-api.service.spec.ts",
)
CONFIG = {
    "failure_surfaces": [
        "hub",
        "sfu",
        "relay",
        "browser",
        "reconciliation-worker",
        "training-worker",
        "store",
    ],
    "persistent_repositories": 4,
    "flask_api_suites": 2,
    "worker_lifecycle_suites": 4,
    "browser_lifecycle_suites": 5,
    "external_live_failover": True,
}


def evaluate_external_live_failover(path: Path) -> tuple[bool, tuple[str, ...], Mapping[str, Any]]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ("external_live_failover_evidence_unavailable",), {}
    if not isinstance(report, Mapping):
        return False, ("external_live_failover_evidence_invalid",), {}
    reasons = recompute_live_failover_evidence(report)
    verified = not reasons
    if report.get("external_live_failover_verified") is not verified:
        reasons.append("external_live_failover_decision_stale")
    if report.get("verdict") != ("pass" if verified else "fail"):
        reasons.append("external_live_failover_verdict_inconsistent")
    reasons = sorted(set(reasons))
    return not reasons, tuple(reasons), report


def run(*, execute: bool, live_failover_report: Path = DEFAULT_LIVE_FAILOVER) -> GateEvidence:
    source_digest = source_hash(ROOT, SOURCES)
    config_digest = canonical_sha256(CONFIG)
    if not execute:
        return unavailable_evidence(
            "ASMP-QA-007",
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="chaos_execution_not_requested",
        )
    python = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *PYTHON_SCENARIOS],
        cwd=ROOT,
        check=False,
        capture_output=True,
        timeout=180,
    )
    browser = subprocess.run(
        ["npx", "vitest", "run", *BROWSER_SCENARIOS],
        cwd=ROOT / "frontend-angular",
        check=False,
        capture_output=True,
        timeout=180,
    )
    reasons = []
    if python.returncode != 0:
        reasons.append("persistent_hub_worker_chaos_failed")
    if browser.returncode != 0:
        reasons.append("browser_lifecycle_chaos_failed")
    live_failover_verified, live_failover_reasons, live_failover = evaluate_external_live_failover(live_failover_report)
    reasons.extend(live_failover_reasons)
    status = "passed" if not reasons else "failed"
    engines = live_failover.get("engines") if isinstance(live_failover.get("engines"), list) else []
    return GateEvidence(
        gate_id="ASMP-QA-007",
        status=status,
        reason_codes=tuple(reasons),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements={
            "failure_surface_count": 7,
            "persistent_repository_count": 4,
            "flask_api_suite_count": 2,
            "worker_lifecycle_suite_count": 4,
            "browser_lifecycle_suite_count": 5,
            "python_exit_code": python.returncode,
            "browser_exit_code": browser.returncode,
            "external_live_failover_verified": live_failover_verified,
            "external_live_failover_browser_engine_count": len(engines),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--external-live-failover", type=Path, default=DEFAULT_LIVE_FAILOVER)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = run(execute=args.execute, live_failover_report=args.external_live_failover)
    if args.output:
        write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
