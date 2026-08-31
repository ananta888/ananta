#!/usr/bin/env python3
"""Aggregate source-bound SFU broadcast evidence into one release decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_release_common import (  # noqa: E402
    atomic_write_report,
    canonical_sha256,
    content_reasons,
    is_sha256,
    parent_activation_reasons,
    parse_utc,
    read_bounded_json,
)

DEFAULT_MANIFEST = ROOT / "config/release/sfu_broadcast_gate_manifest.json"
DEFAULT_TODO = ROOT / "todos/active/todo.webrtc-sfu-broadcast-fanout.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-release.json"
REPORT_SCHEMA = "ananta.sfu-broadcast-release-gate.v1"
GATE_ID = "SFB-GATE-011"
SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\).+$")


def _task(todo: Mapping[str, Any], task_id: str) -> Mapping[str, Any] | None:
    tasks = todo.get("tasks")
    if not isinstance(tasks, list):
        return None
    return next(
        (row for row in tasks if isinstance(row, Mapping) and row.get("id") == task_id),
        None,
    )


def _cross_track_refs(todo: Mapping[str, Any]) -> tuple[str, ...] | None:
    value = todo.get("cross_track_prerequisites")
    if not isinstance(value, list):
        return None
    refs: list[str] = []
    for item in value:
        if isinstance(item, str):
            refs.append(item)
        elif isinstance(item, Mapping):
            direct = item.get("task_ref")
            if isinstance(direct, str):
                refs.append(direct)
            elif isinstance(item.get("todo"), str) and isinstance(item.get("task_id"), str):
                refs.append(f"{item['todo']}:{item['task_id']}")
            else:
                return None
        else:
            return None
    return tuple(sorted(set(refs)))


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest_rows(value: Any) -> dict[str, str] | None:
    if not isinstance(value, list):
        return None
    result: dict[str, str] = {}
    for row in value:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"id", "sha256"}
            or not isinstance(row.get("id"), str)
            or not is_sha256(row.get("sha256"))
            or row["id"] in result
        ):
            return None
        result[row["id"]] = row["sha256"]
    return result


def _validate_attestation(
    *,
    entry: Mapping[str, Any],
    profile_id: str | None,
    verifier: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if profile_id is None:
        return ()
    attestation = entry.get("attestation")
    if not isinstance(attestation, Mapping):
        return ("release_attestation_missing",)
    if attestation.get("profile_id") != profile_id:
        return ("release_attestation_profile_mismatch",)
    if verifier is None:
        return ("release_attestation_verifier_missing",)
    rows = verifier.get("verified_attestations")
    if not isinstance(rows, list):
        return ("release_attestation_verifier_invalid",)
    matched = [
        row for row in rows
        if isinstance(row, Mapping)
        and row.get("artifact_sha256") == entry.get("artifact_sha256")
        and row.get("profile_id") == profile_id
        and row.get("key_id") == attestation.get("key_id")
        and row.get("verified") is True
    ]
    return () if len(matched) == 1 else ("release_attestation_unverified",)


def _release_requirements(
    *,
    gate_manifest: Mapping[str, Any],
    todo: Mapping[str, Any],
    reasons: set[str],
) -> tuple[Mapping[str, Any], set[str]]:
    release_task = _task(todo, GATE_ID)
    if release_task is None:
        reasons.add("release_task_missing")
        dependencies: list[str] = []
    else:
        raw_dependencies = release_task.get("depends_on")
        dependencies = list(raw_dependencies) if isinstance(raw_dependencies, list) else []
        if not dependencies or any(not isinstance(item, str) for item in dependencies):
            reasons.add("release_task_dependencies_invalid")
            dependencies = []

    external_dependencies = tuple(sorted(item for item in dependencies if ":" in item))
    declared_external = _cross_track_refs(todo)
    if declared_external is None:
        reasons.add("release_cross_track_prerequisites_invalid")
    elif external_dependencies != declared_external:
        reasons.add("release_cross_track_prerequisites_mismatch")

    required_gate_ids = {
        item for item in dependencies if ":" not in item and item != GATE_ID
    }
    policy = gate_manifest.get("release_policy")
    if not isinstance(policy, Mapping):
        reasons.add("release_policy_missing")
        policy = {}
    extra = policy.get("extra_required_gate_ids")
    if isinstance(extra, list) and all(isinstance(item, str) for item in extra):
        required_gate_ids.update(extra)
    else:
        reasons.add("release_extra_gate_policy_invalid")
    return policy, required_gate_ids


def _evidence_entries(
    *,
    gate_manifest: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
    required_gate_ids: set[str],
    reasons: set[str],
) -> tuple[dict[str, Mapping[str, Any]], Any]:
    if evidence_manifest.get("schema") != "ananta.sfu-broadcast-evidence-manifest.v1":
        reasons.add("release_evidence_manifest_schema_invalid")
    manifest_version = evidence_manifest.get("manifest_version")
    minimum_version = gate_manifest.get("default_policy", {}).get("minimum_manifest_version")
    if (
        not isinstance(manifest_version, int)
        or not isinstance(minimum_version, int)
        or manifest_version < minimum_version
        or manifest_version < int(gate_manifest.get("manifest_version", 0))
    ):
        reasons.add("release_evidence_manifest_version_stale")

    entries = evidence_manifest.get("entries")
    if not isinstance(entries, list):
        reasons.add("release_evidence_entries_invalid")
        entries = []
    entry_map: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("gate_id"), str):
            reasons.add("release_evidence_entry_invalid")
            continue
        gate_id = entry["gate_id"]
        if gate_id in entry_map:
            reasons.add("release_evidence_gate_duplicate")
        entry_map[gate_id] = entry
    if set(entry_map) < required_gate_ids:
        reasons.add("release_required_gate_evidence_missing")
    return entry_map, manifest_version


def _artifact_binding_reasons(
    entry: Mapping[str, Any],
    *,
    artifact_root: Path,
) -> set[str]:
    reasons: set[str] = set()
    artifact_path = entry.get("artifact_path")
    if not isinstance(artifact_path, str) or SAFE_PATH.fullmatch(artifact_path) is None:
        reasons.add("release_artifact_path_invalid")
        return reasons

    candidate = artifact_root / artifact_path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(artifact_root.resolve())
    except (OSError, ValueError):
        reasons.add("release_artifact_unavailable")
    else:
        if candidate.is_symlink() or not candidate.is_file():
            reasons.add("release_artifact_unavailable")
        elif _artifact_sha256(candidate) != entry.get("artifact_sha256"):
            reasons.add("release_artifact_content_digest_mismatch")
        else:
            artifact = read_bounded_json(candidate)
            if artifact.get("schema") != entry.get("artifact_schema"):
                reasons.add("release_artifact_schema_mismatch")
            if artifact.get("status") != "passed":
                reasons.add("release_artifact_status_not_passed")
    return reasons


def _freshness_reasons(
    entry: Mapping[str, Any],
    *,
    as_of: datetime,
    maximum_age: int,
    future_skew: int,
) -> set[str]:
    reasons: set[str] = set()
    freshness = entry.get("freshness")
    produced = parse_utc(freshness.get("produced_at")) if isinstance(freshness, Mapping) else None
    expires = parse_utc(freshness.get("expires_at")) if isinstance(freshness, Mapping) else None
    if produced is None or expires is None or expires <= produced:
        reasons.add("release_artifact_freshness_invalid")
        return reasons
    if as_of - produced > timedelta(seconds=maximum_age):
        reasons.add("release_artifact_stale")
    if produced - as_of > timedelta(seconds=future_skew):
        reasons.add("release_artifact_from_future")
    if as_of >= expires:
        reasons.add("release_artifact_expired")
    return reasons


def _digest_binding_reasons(
    entry: Mapping[str, Any],
    *,
    gate_policy: Mapping[str, Any] | None,
    digest_registry: dict[tuple[str, str], str],
) -> set[str]:
    reasons: set[str] = set()
    for category in ("config", "lockfile", "image", "infrastructure"):
        rows = _digest_rows(entry.get(f"{category}_digests"))
        if rows is None:
            reasons.add(f"release_{category}_digests_invalid")
            continue
        required_ids = (
            set(gate_policy.get("required_digest_ids", {}).get(category, []))
            if isinstance(gate_policy, Mapping)
            else set()
        )
        if not required_ids.issubset(rows):
            reasons.add(f"release_{category}_digest_missing")
        for digest_id, digest in rows.items():
            key = (category, digest_id)
            if key in digest_registry and digest_registry[key] != digest:
                reasons.add(f"release_{category}_digest_conflict")
            digest_registry[key] = digest
    return reasons


def _evidence_entry_reasons(
    entry: Mapping[str, Any],
    *,
    expected_source_sha256: str | None,
    artifact_root: Path,
    as_of: datetime,
    maximum_age: int,
    future_skew: int,
    gate_policy: Mapping[str, Any] | None,
    digest_registry: dict[tuple[str, str], str],
    attestation_verifier: Mapping[str, Any] | None,
) -> set[str]:
    reasons: set[str] = set()
    if entry.get("status") != "passed":
        reasons.add("release_required_gate_not_passed")
    if entry.get("git_source_digest") != expected_source_sha256:
        reasons.add("release_artifact_source_digest_mismatch")
    reasons.update(_artifact_binding_reasons(entry, artifact_root=artifact_root))
    reasons.update(
        _freshness_reasons(
            entry,
            as_of=as_of,
            maximum_age=maximum_age,
            future_skew=future_skew,
        )
    )
    reasons.update(
        _digest_binding_reasons(
            entry,
            gate_policy=gate_policy,
            digest_registry=digest_registry,
        )
    )
    profile_id = gate_policy.get("attestation_profile") if isinstance(gate_policy, Mapping) else None
    reasons.update(
        _validate_attestation(
            entry=entry,
            profile_id=profile_id,
            verifier=attestation_verifier,
        )
    )
    return reasons


def _verified_evidence_count(
    *,
    gate_manifest: Mapping[str, Any],
    entry_map: Mapping[str, Mapping[str, Any]],
    required_gate_ids: set[str],
    expected_source_sha256: str | None,
    artifact_root: Path,
    as_of: datetime,
    attestation_verifier: Mapping[str, Any] | None,
    reasons: set[str],
) -> int:
    policy_map = {
        row.get("gate_id"): row
        for row in gate_manifest.get("gates", [])
        if isinstance(row, Mapping) and isinstance(row.get("gate_id"), str)
    }
    maximum_age = int(gate_manifest.get("default_policy", {}).get("max_age_seconds", 0))
    future_skew = int(
        gate_manifest.get("default_policy", {}).get("max_future_skew_seconds", 0)
    )
    digest_registry: dict[tuple[str, str], str] = {}
    verified = 0
    for gate_id in sorted(required_gate_ids):
        entry = entry_map.get(gate_id)
        if entry is None:
            continue
        entry_reasons = _evidence_entry_reasons(
            entry,
            expected_source_sha256=expected_source_sha256,
            artifact_root=artifact_root,
            as_of=as_of,
            maximum_age=maximum_age,
            future_skew=future_skew,
            gate_policy=policy_map.get(gate_id),
            digest_registry=digest_registry,
            attestation_verifier=attestation_verifier,
        )
        if entry_reasons:
            reasons.update(entry_reasons)
        else:
            verified += 1
    return verified


def _validate_capacity(
    capacity: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    reasons: set[str],
) -> int:
    if capacity.get("schema") != "ananta.sfu-broadcast-derived-capacity.v1":
        reasons.add("release_capacity_schema_invalid")
    if capacity.get("status") != "passed" or capacity.get("derived") is not True:
        reasons.add("release_capacity_not_derived")
    receiver_cap = capacity.get("receiver_cap")
    if not isinstance(receiver_cap, int) or isinstance(receiver_cap, bool) or receiver_cap < 1:
        reasons.add("release_receiver_cap_invalid")
        receiver_cap = 0
    required_capacity = policy.get("required_capacity_fields", [])
    if any(field not in capacity for field in required_capacity):
        reasons.add("release_capacity_field_missing")
    return receiver_cap


def _validate_rollback(
    rollback: Mapping[str, Any],
    *,
    risk_summary: Mapping[str, Any],
    reasons: set[str],
) -> tuple[bool, bool, bool]:
    if rollback.get("schema") != "ananta.sfu-broadcast-game-day-gate.v1":
        reasons.add("release_rollback_schema_invalid")
    rollback_summary = rollback.get("summary")
    scenario_count = (
        rollback_summary.get("scenario_count") if isinstance(rollback_summary, Mapping) else 0
    )
    atomic_count = (
        rollback_summary.get("atomic_rollback_count")
        if isinstance(rollback_summary, Mapping)
        else -1
    )
    rollback_atomic = (
        rollback.get("status") == "passed"
        and isinstance(scenario_count, int)
        and scenario_count > 0
        and atomic_count == scenario_count
    )
    flags_disabled = isinstance(rollback_summary, Mapping) and rollback_summary.get(
        "all_advanced_flags_disabled"
    ) is True
    kill_switch_verified = risk_summary.get("kill_switch_verified") is True
    if not rollback_atomic:
        reasons.add("release_rollback_not_atomic")
    if not flags_disabled:
        reasons.add("release_advanced_flags_not_disabled")
    if not kill_switch_verified:
        reasons.add("release_kill_switch_unverified")
    return rollback_atomic, flags_disabled, kill_switch_verified


def _validate_risk_summary(
    risk_summary: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    reasons: set[str],
) -> list[Any]:
    if risk_summary.get("schema") != "ananta.sfu-broadcast-risk-summary.v1":
        reasons.add("release_risk_summary_schema_invalid")
    counters = risk_summary.get("open_findings")
    if not isinstance(counters, Mapping):
        reasons.add("release_risk_counters_missing")
        counters = {}
    for name in policy.get("required_zero_counters", []):
        if counters.get(name) != 0:
            reasons.add(f"release_open_finding:{name}")
    residual_risks = risk_summary.get("known_residual_risks")
    if not isinstance(residual_risks, list) or any(
        not isinstance(item, str) or not item for item in residual_risks
    ):
        reasons.add("release_residual_risk_inventory_invalid")
        residual_risks = []
    return residual_risks


def evaluate_release(
    *,
    gate_manifest: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
    todo: Mapping[str, Any],
    parent: Mapping[str, Any],
    capacity: Mapping[str, Any],
    rollback: Mapping[str, Any],
    risk_summary: Mapping[str, Any],
    expected_source_sha256: str | None,
    as_of: datetime,
    artifact_root: Path,
    attestation_verifier: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(
        evidence_manifest, parent, capacity, rollback, risk_summary,
    ))
    reasons.update(parent_activation_reasons(parent))
    if not is_sha256(expected_source_sha256):
        reasons.add("release_expected_source_digest_missing")
    policy, required_gate_ids = _release_requirements(
        gate_manifest=gate_manifest,
        todo=todo,
        reasons=reasons,
    )
    entry_map, manifest_version = _evidence_entries(
        gate_manifest=gate_manifest,
        evidence_manifest=evidence_manifest,
        required_gate_ids=required_gate_ids,
        reasons=reasons,
    )
    verified = _verified_evidence_count(
        gate_manifest=gate_manifest,
        entry_map=entry_map,
        required_gate_ids=required_gate_ids,
        expected_source_sha256=expected_source_sha256,
        artifact_root=artifact_root,
        as_of=as_of,
        attestation_verifier=attestation_verifier,
        reasons=reasons,
    )
    receiver_cap = _validate_capacity(capacity, policy=policy, reasons=reasons)
    rollback_atomic, flags_disabled, kill_switch_verified = _validate_rollback(
        rollback,
        risk_summary=risk_summary,
        reasons=reasons,
    )
    residual_risks = _validate_risk_summary(
        risk_summary,
        policy=policy,
        reasons=reasons,
    )

    passed = not reasons
    return {
        "schema": REPORT_SCHEMA,
        "gate_id": GATE_ID,
        "status": "passed" if passed else "failed",
        "decision": "go" if passed else "no_go",
        "activation_allowed": passed,
        "reason_codes": sorted(reasons),
        "bindings": {
            "source_sha256": expected_source_sha256 if is_sha256(expected_source_sha256) else None,
            "gate_manifest_sha256": canonical_sha256(gate_manifest),
            "evidence_manifest_sha256": canonical_sha256(evidence_manifest),
            "capacity_sha256": canonical_sha256(capacity),
            "rollback_sha256": canonical_sha256(rollback),
        },
        "released_scopes": sorted(capacity.get("released_scopes", [])) if passed else [],
        "receiver_cap": receiver_cap if passed else 0,
        "slo_budgets": capacity.get("slo_budgets", {}) if passed else {},
        "resource_budgets": capacity.get("resource_budgets", {}) if passed else {},
        "versions": {
            "browsers": capacity.get("browser_versions", {}) if passed else {},
            "sfu": capacity.get("sfu_version") if passed else None,
            "turn": capacity.get("turn_version") if passed else None,
        },
        "known_residual_risks": residual_risks,
        "evidence_summary": {
            "required_gate_count": len(required_gate_ids),
            "verified_gate_count": verified,
            "manifest_version": manifest_version if isinstance(manifest_version, int) else 0,
        },
        "rollback_summary": {
            "atomic": rollback_atomic,
            "kill_switch_verified": kill_switch_verified,
            "all_advanced_flags_disabled": flags_disabled,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-manifest", type=Path)
    parser.add_argument("--todo", type=Path, default=DEFAULT_TODO)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--capacity-profile", type=Path)
    parser.add_argument("--rollback-evidence", type=Path)
    parser.add_argument("--risk-summary", type=Path)
    parser.add_argument("--attestation-verifier-result", type=Path)
    parser.add_argument(
        "--expected-source-digest",
        default=os.environ.get("ANANTA_CHILD_SOURCE_SHA256"),
    )
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    required = (
        args.evidence_manifest, args.parent_evidence, args.capacity_profile,
        args.rollback_evidence, args.risk_summary,
    )
    if any(path is None for path in required):
        report = {
            "schema": REPORT_SCHEMA,
            "gate_id": GATE_ID,
            "status": "failed",
            "decision": "no_go",
            "activation_allowed": False,
            "reason_codes": ["release_required_input_missing"],
            "bindings": {
                "source_sha256": None,
                "gate_manifest_sha256": canonical_sha256(read_bounded_json(args.gate_manifest)),
                "evidence_manifest_sha256": canonical_sha256({}),
                "capacity_sha256": canonical_sha256({}),
                "rollback_sha256": canonical_sha256({}),
            },
            "released_scopes": [],
            "receiver_cap": 0,
            "slo_budgets": {},
            "resource_budgets": {},
            "versions": {"browsers": {}, "sfu": None, "turn": None},
            "known_residual_risks": [],
            "evidence_summary": {
                "required_gate_count": 0, "verified_gate_count": 0,
                "manifest_version": 0,
            },
            "rollback_summary": {
                "atomic": False, "kill_switch_verified": False,
                "all_advanced_flags_disabled": False,
            },
        }
    else:
        as_of = (
            datetime.fromisoformat(args.as_of.replace("Z", "+00:00")).astimezone(UTC)
            if args.as_of
            else datetime.now(UTC)
        )
        report = evaluate_release(
            gate_manifest=read_bounded_json(args.gate_manifest),
            evidence_manifest=read_bounded_json(args.evidence_manifest),
            todo=read_bounded_json(args.todo),
            parent=read_bounded_json(args.parent_evidence),
            capacity=read_bounded_json(args.capacity_profile),
            rollback=read_bounded_json(args.rollback_evidence),
            risk_summary=read_bounded_json(args.risk_summary),
            expected_source_sha256=args.expected_source_digest,
            as_of=as_of,
            artifact_root=ROOT,
            attestation_verifier=(
                read_bounded_json(args.attestation_verifier_result)
                if args.attestation_verifier_result else None
            ),
        )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
