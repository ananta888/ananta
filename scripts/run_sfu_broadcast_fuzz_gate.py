#!/usr/bin/env python3
"""Bounded deterministic contract/state/queue fuzz gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent.services.sfu_broadcast_contract_materialization import (
    ContractMaterializationError,
    ContractMaterializationReason,
    ContractReaderCompatibility,
    ContractVersionDescriptor,
    SfuBroadcastContractMaterializer,
)

try:
    from scripts.e2e.sfu_broadcast_harness import DEFAULT_PROFILE, load_acceptance_profile
    from scripts.sfu_broadcast_gate_common import (
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        run_bounded_command,
        utc_now,
    )
except ModuleNotFoundError:
    from e2e.sfu_broadcast_harness import DEFAULT_PROFILE, load_acceptance_profile  # type: ignore[no-redef]
    from sfu_broadcast_gate_common import (  # type: ignore[no-redef]
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        run_bounded_command,
        utc_now,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-fuzz.json"
FUZZ_SOURCES = (
    "agent/services/sfu_broadcast_contract_materialization.py",
    "agent/services/sfu_broadcast_contract_validator.py",
    "agent/services/sfu_fanout_route_lifecycle.py",
    "agent/services/sfu_broadcast_data_port.py",
    "agent/services/sfu_broadcast_data_queue_policy.py",
    "tests/fuzz/test_sfu_broadcast_contract_fuzz.py",
    "tests/fuzz/test_sfu_broadcast_state_machine_fuzz.py",
)
COVERAGE = (
    "truncation",
    "oversize",
    "depth",
    "unknown_field",
    "unicode",
    "integer_boundary",
    "duplicate",
    "reorder",
    "gap",
    "replay",
    "epoch_regression",
    "fencing_regression",
    "signature_manipulation",
    "message_flood",
    "priority_inversion",
    "blocked_receiver",
    "malformed_adapter_event",
)


def _known_blockers() -> list[str]:
    descriptor = ContractVersionDescriptor("fuzz.contract.v1", "1", 1)
    compatibility = ContractReaderCompatibility(
        schema_versions={"fuzz.contract.v1": frozenset({"1"})}
    )
    try:
        materialized = SfuBroadcastContractMaterializer().materialize(
            '{"scope":"first","scope":"second"}',
            descriptor,
            compatibility,
        )
    except ContractMaterializationError as exc:
        if exc.reason_code is ContractMaterializationReason.DUPLICATE_PROPERTY:
            return []
        # An inconclusive probe blocks without exposing parser detail or input data.
        return ["duplicate_json_key_probe_inconclusive"]
    return (
        ["duplicate_json_key_not_rejected"]
        if materialized.document.get("scope") == "second"
        else []
    )


def run_gate(*, profile_path: Path, output: Path, plan_only: bool) -> dict:
    profile = load_acceptance_profile(profile_path)
    started_at = None if plan_only else utc_now()
    command_result = None
    reasons = ["fuzz_execution_not_requested"] if plan_only else []
    blockers = _known_blockers()
    if not plan_only:
        env = dict(os.environ)
        env["SFU_BROADCAST_FUZZ_SEEDS"] = ",".join(str(seed) for seed in profile.seeds)
        env["SFU_BROADCAST_FUZZ_CASES_PER_SEED"] = str(profile.limits["cases_per_seed_min"])
        command_result = run_bounded_command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/fuzz/test_sfu_broadcast_contract_fuzz.py",
                "tests/fuzz/test_sfu_broadcast_state_machine_fuzz.py",
                "tests/test_sfu_broadcast_contract_validator.py",
                "tests/contracts/test_sfu_broadcast_data_port.py",
                "tests/contracts/test_sfu_broadcast_data_queue_policy.py",
            ],
            cwd=ROOT,
            env=env,
            timeout_seconds=profile.limits["scenario_timeout_seconds"],
            cpu_seconds_max=profile.limits["cpu_seconds_max"],
            memory_bytes_max=profile.limits["memory_bytes_max"],
        )
        if command_result.timed_out:
            reasons.append("fuzz_timeout")
        elif command_result.exit_code != 0:
            reasons.append("fuzz_cases_failed")
        reasons.extend(blockers)
    status = "unverified" if plan_only else ("passed" if not reasons else "failed")
    measurements = {
        "configured_timeout_seconds": profile.limits["scenario_timeout_seconds"],
        "configured_cpu_seconds_max": profile.limits["cpu_seconds_max"],
        "configured_memory_bytes_max": profile.limits["memory_bytes_max"],
        "elapsed_ms": command_result.elapsed_ms if command_result else 0,
        "cpu_seconds": command_result.cpu_seconds if command_result else 0,
        "peak_rss_bytes": command_result.peak_rss_bytes if command_result else 0,
        "exit_code": command_result.exit_code if command_result else None,
        "timed_out": command_result.timed_out if command_result else False,
    }
    report = {
        "schema": "ananta.sfu-broadcast-fuzz-gate.v1",
        "gate_id": "SFB-GATE-002",
        "status": status,
        "release_blocking": status != "passed",
        "reason_codes": sorted(set(reasons)),
        "evidence_scope": "local_contract_state_queue_only",
        "real_media_plane_claimed": False,
        "source_sha256": digest_paths(ROOT, FUZZ_SOURCES),
        "config_sha256": canonical_sha256(profile.document),
        "corpus_sha256": canonical_sha256(
            {"seeds": profile.seeds, "cases_per_seed": profile.limits["cases_per_seed_min"], "coverage": COVERAGE}
        ),
        "seeds": list(profile.seeds),
        "cases_per_seed": profile.limits["cases_per_seed_min"],
        "coverage": list(COVERAGE),
        "known_blockers": blockers,
        "minimized_failure": None,
        "started_at": started_at,
        "ended_at": None if plan_only else utc_now(),
        "measurements": measurements,
    }
    atomic_write_report(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    try:
        report = run_gate(profile_path=args.profile, output=args.output, plan_only=args.plan_only)
    except SfuBroadcastGateError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
