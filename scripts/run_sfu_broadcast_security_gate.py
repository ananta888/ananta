#!/usr/bin/env python3
"""Fail-closed local plus external SFU broadcast security gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent.services.sfu_broadcast_privacy_sentinel import (
    SfuBroadcastPrivacyScanConfigurationError,
    scan_sfu_broadcast_privacy_surfaces,
)

try:
    from scripts.e2e.sfu_broadcast_harness import (
        DEFAULT_PROFILE,
        environment_digests,
        evaluate_real_media_result,
        load_acceptance_profile,
    )
    from scripts.sfu_broadcast_gate_common import (
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        read_bounded_json,
        run_bounded_command,
        utc_now,
    )
except ModuleNotFoundError:
    from e2e.sfu_broadcast_harness import (  # type: ignore[no-redef]
        DEFAULT_PROFILE,
        environment_digests,
        evaluate_real_media_result,
        load_acceptance_profile,
    )
    from sfu_broadcast_gate_common import (  # type: ignore[no-redef]
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        read_bounded_json,
        run_bounded_command,
        utc_now,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-security.json"
SECURITY_SOURCES = (
    "agent/services/sfu_broadcast_privacy_sentinel.py",
    "agent/services/sfu_member_digest_key_contract.py",
    "agent/services/sfu_fanout_route_lifecycle.py",
    "scripts/scan_sfu_broadcast_privacy_sentinels.py",
    "tests/security/test_sfu_broadcast_gate_security.py",
    "tests/security/test_sfu_broadcast_privacy_sentinels.py",
)
ATTACK_CLASSES = (
    "idor",
    "cross_tenant",
    "cross_room",
    "cross_publication",
    "cross_device",
    "cross_node",
    "publish_subscribe_confusion",
    "receive_only_escalation",
    "stale_consent",
    "token_replay",
    "epoch_regression",
    "fencing_regression",
    "toctou",
    "compromised_node",
)


def run_gate(
    *,
    profile_path: Path,
    output: Path,
    privacy_root: Path | None,
    privacy_manifest: Path | None,
    sentinels: Path | None,
    external_result: Path | None,
    plan_only: bool,
) -> dict:
    profile = load_acceptance_profile(profile_path)
    started_at = None if plan_only else utc_now()
    reasons: list[str] = ["security_execution_not_requested"] if plan_only else []
    command_result = None
    privacy_summary = {"decision": "blocked", "finding_count": 0}
    external_verified = False
    if not plan_only:
        command_result = run_bounded_command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/security/test_sfu_broadcast_gate_security.py",
                "tests/security/test_sfu_broadcast_privacy_sentinels.py",
                "tests/security/test_sfu_member_digest_key_contract.py",
            ],
            cwd=ROOT,
            env=dict(os.environ),
            timeout_seconds=profile.limits["scenario_timeout_seconds"],
            cpu_seconds_max=profile.limits["cpu_seconds_max"],
            memory_bytes_max=profile.limits["memory_bytes_max"],
        )
        if command_result.timed_out:
            reasons.append("security_test_timeout")
        elif command_result.exit_code != 0:
            reasons.append("security_tests_failed")
        if privacy_root is None or privacy_manifest is None or sentinels is None:
            reasons.append("real_privacy_surfaces_unavailable")
        else:
            try:
                privacy_report = scan_sfu_broadcast_privacy_surfaces(
                    root=privacy_root,
                    manifest_document=read_bounded_json(privacy_manifest),
                    sentinel_document=read_bounded_json(sentinels),
                )
            except SfuBroadcastPrivacyScanConfigurationError as exc:
                reasons.append(exc.reason_code)
            else:
                privacy_summary = {
                    "decision": privacy_report["decision"],
                    "finding_count": privacy_report["measurements"]["finding_count"],
                    "manifest_sha256": privacy_report["manifest_sha256"],
                }
                if privacy_report["decision"] != "allow":
                    reasons.extend(privacy_report["reason_codes"])
        if external_result is None:
            reasons.append("external_browser_container_media_evidence_unavailable")
        else:
            external_document = read_bounded_json(external_result)
            external_reasons = evaluate_real_media_result(
                external_document,
                profile=profile,
                expected_digests=environment_digests(profile),
            )
            reasons.extend(external_reasons)
            external_verified = not external_reasons
    status = "unverified" if plan_only else (
        "passed" if not reasons and external_verified and privacy_summary["decision"] == "allow" else
        "failed" if any(reason not in {
            "real_privacy_surfaces_unavailable",
            "external_browser_container_media_evidence_unavailable",
        } for reason in reasons) else
        "blocked"
    )
    report = {
        "schema": "ananta.sfu-broadcast-security-gate.v1",
        "gate_id": "SFB-GATE-003",
        "status": status,
        "release_blocking": status != "passed",
        "reason_codes": sorted(set(reasons)),
        "source_sha256": digest_paths(ROOT, SECURITY_SOURCES),
        "config_sha256": canonical_sha256(profile.document),
        "attack_classes": list(ATTACK_CLASSES),
        "external_real_media_verified": external_verified,
        "privacy_scan": privacy_summary,
        "content_free_audit_required": True,
        "started_at": started_at,
        "ended_at": None if plan_only else utc_now(),
        "measurements": {
            "elapsed_ms": command_result.elapsed_ms if command_result else 0,
            "cpu_seconds": command_result.cpu_seconds if command_result else 0,
            "peak_rss_bytes": command_result.peak_rss_bytes if command_result else 0,
            "exit_code": command_result.exit_code if command_result else None,
            "timed_out": command_result.timed_out if command_result else False,
        },
    }
    atomic_write_report(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--privacy-root", type=Path)
    parser.add_argument("--privacy-manifest", type=Path)
    parser.add_argument("--sentinels", type=Path)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    try:
        report = run_gate(
            profile_path=args.profile,
            output=args.output,
            privacy_root=args.privacy_root,
            privacy_manifest=args.privacy_manifest,
            sentinels=args.sentinels,
            external_result=args.external_result,
            plan_only=args.plan_only,
        )
    except SfuBroadcastGateError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

