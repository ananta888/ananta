#!/usr/bin/env python3
"""Validate externally produced real-browser SFU broadcast evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.e2e.sfu_broadcast_harness import (
        DEFAULT_PROFILE,
        build_manifest,
        load_acceptance_profile,
    )
    from scripts.sfu_broadcast_gate_common import (
        SfuBroadcastGateError,
        atomic_write_report,
        read_bounded_json,
    )
except ModuleNotFoundError:
    from sfu_broadcast_harness import DEFAULT_PROFILE, build_manifest, load_acceptance_profile  # type: ignore[no-redef]
    from sfu_broadcast_gate_common import (  # type: ignore[no-redef]
        SfuBroadcastGateError,
        atomic_write_report,
        read_bounded_json,
    )


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "config/test-profiles/sfu-broadcast/browser-matrix.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-cross-browser.json"


def validate_matrix(document) -> tuple[str, ...]:
    reasons: list[str] = []
    if document.get("schema") != "ananta.sfu-broadcast-browser-matrix.v1":
        reasons.append("browser_matrix_schema_invalid")
    combinations = document.get("combinations") if isinstance(document.get("combinations"), list) else []
    if not combinations:
        reasons.append("browser_matrix_empty")
    for item in combinations:
        if not isinstance(item, dict):
            reasons.append("browser_matrix_entry_invalid")
            continue
        if item.get("browser") in {"safari", "ios_safari"} and item.get("evidence_source") in {
            "playwright_webkit",
            "mobile_viewport",
        }:
            reasons.append("simulated_safari_claim_forbidden")
        if item.get("status") == "unsupported" and item.get("fallback") != "parent_ordinary_media":
            reasons.append("browser_matrix_fallback_missing")
    return tuple(sorted(set(reasons)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        matrix = read_bounded_json(args.matrix)
        matrix_reasons = validate_matrix(matrix)
        profile = load_acceptance_profile(args.profile)
        external = read_bounded_json(args.external_result) if args.external_result else None
        report = build_manifest(profile=profile, external_result=external)
        reasons = sorted(set((*report["reason_codes"], *matrix_reasons)))
        report = {
            **report,
            "schema": "ananta.sfu-broadcast-cross-browser-gate.v1",
            "gate_id": "SFB-GATE-004",
            "status": "passed" if not reasons else ("blocked" if external is None and not matrix_reasons else "failed"),
            "release_blocking": bool(reasons),
            "reason_codes": reasons,
            "browser_matrix_sha256": __import__("hashlib").sha256(
                json.dumps(matrix, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        atomic_write_report(args.output, report)
    except SfuBroadcastGateError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

