#!/usr/bin/env python3
"""Fail-closed release gate for the Kanban and model-dashboard surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = (
    ROOT / "config/test-profiles/kanban-model-dashboard/release-gate.v1.json"
)
DEFAULT_EVIDENCE_DIR = ROOT / "artifacts/e2e/kanban-model-dashboard"
DEFAULT_OUTPUT = (
    ROOT / "artifacts/test-gates/kanban-model-dashboard-release.json"
)
PROFILE_SCHEMA = "ananta.kanban-model-dashboard.release-profile.v1"
EVIDENCE_SCHEMA = "ananta.kanban-model-dashboard.evidence.v1"
OUTPUT_SCHEMA = "ananta.kanban-model-dashboard.release-result.v1"


class ReleaseGateError(ValueError):
    """Raised for malformed gate configuration or invocation."""


@dataclass(frozen=True)
class EvidenceResult:
    suite: str
    path: str
    sha256: str | None
    status: str
    reason_codes: tuple[str, ...]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseGateError(f"unreadable_json:{path}") from exc
    if len(raw) > 2_000_000:
        raise ReleaseGateError(f"oversized_json:{path}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"invalid_json:{path}") from exc
    if not isinstance(value, dict):
        raise ReleaseGateError(f"json_object_required:{path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_profile(profile: Mapping[str, Any]) -> tuple[str, ...]:
    reasons: set[str] = set()
    if profile.get("schema") != PROFILE_SCHEMA:
        reasons.add("profile_schema_invalid")
    suites = profile.get("required_suites")
    if (
        not isinstance(suites, list)
        or not suites
        or any(not isinstance(item, str) or not item for item in suites)
        or len(set(suites)) != len(suites)
    ):
        reasons.add("profile_required_suites_invalid")
    max_age = profile.get("max_age_seconds")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        reasons.add("profile_max_age_invalid")
    pattern = profile.get("evidence_file_pattern")
    if pattern != "{suite}.json":
        reasons.add("profile_evidence_pattern_invalid")
    stages = profile.get("rollout_stages")
    if not isinstance(stages, list) or len(stages) != 4:
        reasons.add("profile_rollout_stages_invalid")
    else:
        expected = {
            "read_only_models",
            "read_only_board",
            "board_writes",
            "allowlisted_default_selection",
        }
        stage_ids = {
            item.get("id")
            for item in stages
            if isinstance(item, Mapping)
        }
        if stage_ids != expected:
            reasons.add("profile_rollout_stages_invalid")
    excluded = set(profile.get("excluded_actions") or ())
    required_exclusions = {
        "worker_start",
        "worker_orchestration",
        "model_load",
        "model_unload",
        "direct_provider_url",
        "shell_command",
    }
    if not required_exclusions.issubset(excluded):
        reasons.add("profile_excluded_actions_incomplete")
    return tuple(sorted(reasons))


def _validate_evidence(
    *,
    suite: str,
    path: Path,
    expected_commit: str,
    as_of: datetime,
    max_age_seconds: int,
) -> EvidenceResult:
    reasons: set[str] = set()
    if not path.is_file() or path.is_symlink():
        return EvidenceResult(
            suite=suite,
            path=str(path),
            sha256=None,
            status="failed",
            reason_codes=("evidence_missing_or_unsafe",),
        )
    try:
        evidence = _read_json(path)
        digest = _sha256(path)
    except ReleaseGateError as exc:
        return EvidenceResult(
            suite=suite,
            path=str(path),
            sha256=None,
            status="failed",
            reason_codes=(str(exc).split(":", 1)[0],),
        )

    if evidence.get("schema") != EVIDENCE_SCHEMA:
        reasons.add("evidence_schema_invalid")
    if evidence.get("suite") != suite:
        reasons.add("evidence_suite_mismatch")
    if evidence.get("status") != "passed":
        reasons.add("evidence_status_not_passed")
    if evidence.get("commit_sha") != expected_commit:
        reasons.add("evidence_commit_mismatch")
    produced_at = _parse_utc(evidence.get("produced_at"))
    if produced_at is None:
        reasons.add("evidence_timestamp_invalid")
    else:
        age = (as_of - produced_at).total_seconds()
        if age < 0:
            reasons.add("evidence_from_future")
        elif age > max_age_seconds:
            reasons.add("evidence_stale")
    input_hashes = evidence.get("input_hashes")
    if not isinstance(input_hashes, Mapping) or not input_hashes:
        reasons.add("evidence_input_hashes_missing")
    elif any(
        not isinstance(name, str)
        or not name
        or not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for name, value in input_hashes.items()
    ):
        reasons.add("evidence_input_hashes_invalid")

    return EvidenceResult(
        suite=suite,
        path=str(path),
        sha256=digest,
        status="passed" if not reasons else "failed",
        reason_codes=tuple(sorted(reasons)),
    )


def run_gate(
    *,
    profile_path: Path,
    evidence_dir: Path,
    commit_sha: str,
    as_of: datetime,
) -> dict[str, Any]:
    if (
        len(commit_sha) not in {40, 64}
        or any(char not in "0123456789abcdef" for char in commit_sha)
    ):
        raise ReleaseGateError("commit_sha_invalid")
    profile = _read_json(profile_path)
    profile_reasons = _validate_profile(profile)
    suites = profile.get("required_suites")
    if not isinstance(suites, list):
        suites = []
    max_age = profile.get("max_age_seconds")
    if not isinstance(max_age, int) or isinstance(max_age, bool):
        max_age = 0

    evidence_results = [
        _validate_evidence(
            suite=suite,
            path=evidence_dir / f"{suite}.json",
            expected_commit=commit_sha,
            as_of=as_of,
            max_age_seconds=max_age,
        )
        for suite in suites
        if isinstance(suite, str)
    ]
    reason_codes = set(profile_reasons)
    for result in evidence_results:
        reason_codes.update(
            f"{result.suite}:{reason}" for reason in result.reason_codes
        )

    status = "passed" if not reason_codes else "failed"
    stages = []
    for stage in profile.get("rollout_stages") or ():
        if not isinstance(stage, Mapping):
            continue
        required = stage.get("required_suites") or ()
        stage_reasons = sorted(
            reason
            for result in evidence_results
            if result.suite in required
            for reason in result.reason_codes
        )
        stages.append(
            {
                "id": stage.get("id"),
                "status": "passed" if not profile_reasons and not stage_reasons else "failed",
                "reason_codes": stage_reasons,
            }
        )

    return {
        "schema": OUTPUT_SCHEMA,
        "profile_id": profile.get("profile_id"),
        "status": status,
        "fail_closed": True,
        "commit_sha": commit_sha,
        "evaluated_at": as_of.isoformat().replace("+00:00", "Z"),
        "profile_sha256": _sha256(profile_path),
        "reason_codes": sorted(reason_codes),
        "evidence": [
            {
                "suite": result.suite,
                "path": result.path,
                "sha256": result.sha256,
                "status": result.status,
                "reason_codes": list(result.reason_codes),
            }
            for result in evidence_results
        ],
        "rollout_stages": stages,
        "excluded_actions": profile.get("excluded_actions"),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    as_of = _parse_utc(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of is None:
        raise SystemExit("invalid --as-of; expected timezone-aware ISO-8601")
    try:
        result = run_gate(
            profile_path=args.profile,
            evidence_dir=args.evidence_dir,
            commit_sha=args.commit_sha,
            as_of=as_of,
        )
    except ReleaseGateError as exc:
        result = {
            "schema": OUTPUT_SCHEMA,
            "status": "failed",
            "fail_closed": True,
            "reason_codes": [str(exc)],
        }
    _write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
