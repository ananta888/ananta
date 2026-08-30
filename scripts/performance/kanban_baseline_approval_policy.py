#!/usr/bin/env python3
"""Fail-closed hub policy for automatic Kanban baseline promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
POLICY_SCHEMA = "ananta.kanban-model-dashboard.baseline-approval-policy.v1"
BASELINE_SCHEMA = "ananta.kanban-model-dashboard.performance-baseline.v1"
PROFILE_SCHEMA = "ananta.kanban-model-dashboard.performance-profile.v1"
APPROVAL_METHOD = "hub_policy"
DEFAULT_POLICY = (
    ROOT
    / "config/test-profiles/kanban-model-dashboard/"
    "baseline-approval-policy.v1.json"
)
DEFAULT_PROFILE = (
    ROOT
    / "config/test-profiles/kanban-model-dashboard/formal-performance.v1.json"
)
DEFAULT_CANDIDATE = (
    ROOT
    / "artifacts/test-gates/"
    "kanban-model-dashboard-performance-baseline-candidate.v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "config/test-profiles/kanban-model-dashboard/baselines/"
    "formal-performance-approved.v1.json"
)
MAX_JSON_BYTES = 4 * 1024 * 1024
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_SOURCES = ("backend", "angular", "tui", "pty")


class BaselineApprovalError(ValueError):
    """Raised when automatic promotion cannot be authorized safely."""


@dataclass(frozen=True, slots=True)
class GitState:
    head_sha: str
    changed_paths: tuple[str, ...]


GitStateReader = Callable[[Path], GitState]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise BaselineApprovalError("path_outside_repository") from exc


def _read_json(root: Path, path: Path) -> tuple[dict[str, Any], bytes, str]:
    relative = _safe_relative(root, path)
    if path.is_symlink() or not path.is_file():
        raise BaselineApprovalError(f"unsafe_json:{relative}")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise BaselineApprovalError(f"oversized_json:{relative}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineApprovalError(f"invalid_json:{relative}") from exc
    if not isinstance(value, dict):
        raise BaselineApprovalError(f"json_object_required:{relative}")
    return value, raw, relative


def _parse_utc(value: object, reason: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BaselineApprovalError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BaselineApprovalError(reason) from exc
    if parsed.tzinfo is None:
        raise BaselineApprovalError(reason)
    return parsed.astimezone(timezone.utc)


def _read_git_state(root: Path) -> GitState:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineApprovalError("git_state_unavailable") from exc
    changed = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        changed.append(path)
    return GitState(head_sha=head, changed_paths=tuple(changed))


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if (
        policy.get("schema") != POLICY_SCHEMA
        or policy.get("policy_version") != 1
        or policy.get("enabled") is not True
        or policy.get("authority") != "hub-control-plane"
    ):
        raise BaselineApprovalError("approval_policy_invalid")
    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"].strip():
        raise BaselineApprovalError("approval_policy_id_invalid")
    principal = policy.get("approval_principal")
    if not isinstance(principal, str) or principal != f"hub-policy:{policy['policy_id']}":
        raise BaselineApprovalError("approval_policy_principal_invalid")
    if policy.get("required_candidate_status") != "candidate_unapproved":
        raise BaselineApprovalError("approval_policy_candidate_status_invalid")
    max_age = policy.get("max_candidate_age_seconds")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        raise BaselineApprovalError("approval_policy_max_age_invalid")
    if policy.get("required_source_artifacts") != list(REQUIRED_SOURCES):
        raise BaselineApprovalError("approval_policy_sources_invalid")
    if policy.get("require_absolute_budgets") is not True:
        raise BaselineApprovalError("approval_policy_budget_rule_invalid")


def _protected_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {"approval_status", "approved_by", "approved_at", "approval"}
    }


def protected_candidate_sha256(candidate: Mapping[str, Any]) -> str:
    """Return the digest of candidate fields protected by an approval."""

    return _canonical_sha256(_protected_candidate(candidate))


def _validate_source_artifacts(
    *, root: Path, candidate: Mapping[str, Any], required: Sequence[str]
) -> None:
    sources = candidate.get("source_artifacts")
    if not isinstance(sources, Mapping) or set(sources) != set(required):
        raise BaselineApprovalError("candidate_source_artifacts_invalid")
    for source in required:
        item = sources.get(source)
        if not isinstance(item, Mapping):
            raise BaselineApprovalError("candidate_source_artifacts_invalid")
        relative = item.get("path")
        digest = item.get("sha256")
        schema = item.get("schema")
        if (
            not isinstance(relative, str)
            or not relative
            or not isinstance(digest, str)
            or HEX_64.fullmatch(digest) is None
            or not isinstance(schema, str)
            or not schema
        ):
            raise BaselineApprovalError("candidate_source_artifacts_invalid")
        path = root / relative
        actual_relative = _safe_relative(root, path)
        if actual_relative != relative or path.is_symlink() or _sha256(path.read_bytes()) != digest:
            raise BaselineApprovalError(f"candidate_source_artifact_mismatch:{source}")


def _validate_candidate(
    *,
    root: Path,
    candidate: Mapping[str, Any],
    candidate_relative: str,
    profile: Mapping[str, Any],
    profile_sha256: str,
    policy: Mapping[str, Any],
    git_state: GitState,
    as_of: datetime,
) -> None:
    if candidate.get("schema") != BASELINE_SCHEMA or candidate.get("baseline_version") != 1:
        raise BaselineApprovalError("candidate_schema_invalid")
    if candidate.get("approval_status") != policy["required_candidate_status"]:
        raise BaselineApprovalError("candidate_status_invalid")
    if candidate.get("approved_by") is not None or candidate.get("approved_at") is not None:
        raise BaselineApprovalError("candidate_preapproved")
    if profile.get("schema") != PROFILE_SCHEMA or profile.get("profile_id") != policy.get("profile_id"):
        raise BaselineApprovalError("approval_profile_invalid")
    candidate_profile = candidate.get("profile")
    if not isinstance(candidate_profile, Mapping) or candidate_profile != {
        "id": profile.get("profile_id"),
        "schema": profile.get("schema"),
        "sha256": profile_sha256,
    }:
        raise BaselineApprovalError("candidate_profile_mismatch")
    commit = candidate.get("commit")
    if (
        not isinstance(commit, Mapping)
        or not isinstance(commit.get("sha"), str)
        or HEX_40.fullmatch(commit["sha"]) is None
        or commit["sha"] != git_state.head_sha
    ):
        raise BaselineApprovalError("candidate_commit_mismatch")
    unexpected_changes = set(git_state.changed_paths) - {candidate_relative}
    if unexpected_changes:
        raise BaselineApprovalError("worktree_contains_unapproved_changes")
    created_at = _parse_utc(candidate.get("candidate_created_at"), "candidate_timestamp_invalid")
    age = (as_of - created_at).total_seconds()
    if age < 0 or age > policy["max_candidate_age_seconds"]:
        raise BaselineApprovalError("candidate_freshness_invalid")
    absolute = candidate.get("absolute_evaluation")
    if not isinstance(absolute, Mapping) or absolute.get("within_budget") is not True:
        raise BaselineApprovalError("candidate_absolute_budget_failed")
    checks = absolute.get("checks")
    if not isinstance(checks, Mapping) or not checks or any(
        not isinstance(check, Mapping) or check.get("passed") is not True
        for check in checks.values()
    ):
        raise BaselineApprovalError("candidate_absolute_checks_invalid")
    compatibility = candidate.get("environment")
    if (
        not isinstance(compatibility, Mapping)
        or not isinstance(compatibility.get("compatibility"), Mapping)
        or not isinstance(compatibility.get("compatibility_sha256"), str)
        or HEX_64.fullmatch(compatibility["compatibility_sha256"]) is None
        or _canonical_sha256(compatibility["compatibility"])
        != compatibility["compatibility_sha256"]
    ):
        raise BaselineApprovalError("candidate_environment_invalid")
    _validate_source_artifacts(
        root=root,
        candidate=candidate,
        required=policy["required_source_artifacts"],
    )


def validate_policy_approval(
    *,
    baseline: Mapping[str, Any],
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> bool:
    """Validate the self-contained, tamper-evident policy attestation."""

    try:
        _validate_policy(policy)
        _parse_utc(baseline.get("approved_at"), "baseline_approval_timestamp_invalid")
    except BaselineApprovalError:
        return False
    approval = baseline.get("approval")
    if not isinstance(approval, Mapping):
        return False
    protected_sha = protected_candidate_sha256(baseline)
    return bool(
        baseline.get("approval_status") == "approved"
        and baseline.get("approved_by") == policy.get("approval_principal")
        and approval.get("method") == APPROVAL_METHOD
        and approval.get("policy_id") == policy.get("policy_id")
        and approval.get("policy_version") == policy.get("policy_version")
        and approval.get("policy_sha256") == policy_sha256
        and approval.get("candidate_commit_sha")
        == (baseline.get("commit") or {}).get("sha")
        and approval.get("protected_payload_sha256") == protected_sha
        and isinstance(approval.get("candidate_sha256"), str)
        and HEX_64.fullmatch(approval["candidate_sha256"]) is not None
        and approval.get("decision") == "approved"
    )


def promote_candidate(
    *,
    root: Path,
    candidate_path: Path,
    profile_path: Path,
    policy_path: Path,
    as_of: datetime | None = None,
    git_state_reader: GitStateReader = _read_git_state,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    candidate, candidate_raw, candidate_relative = _read_json(root, candidate_path)
    profile, profile_raw, _profile_relative = _read_json(root, profile_path)
    policy, policy_raw, _policy_relative = _read_json(root, policy_path)
    _validate_policy(policy)
    current_time = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    git_state = git_state_reader(root)
    _validate_candidate(
        root=root,
        candidate=candidate,
        candidate_relative=candidate_relative,
        profile=profile,
        profile_sha256=_sha256(profile_raw),
        policy=policy,
        git_state=git_state,
        as_of=current_time,
    )
    approved = dict(candidate)
    approved["approval_status"] = "approved"
    approved["approved_by"] = policy["approval_principal"]
    approved["approved_at"] = current_time.isoformat()
    approved["approval"] = {
        "method": APPROVAL_METHOD,
        "decision": "approved",
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_sha256": _sha256(policy_raw),
        "candidate_sha256": _sha256(candidate_raw),
        "candidate_commit_sha": git_state.head_sha,
        "protected_payload_sha256": protected_candidate_sha256(approved),
        "checks": [
            "candidate_commit_matches_head",
            "worktree_changes_bounded_to_candidate",
            "profile_hash_matches",
            "source_artifact_hashes_match",
            "environment_hash_matches",
            "absolute_budgets_pass",
            "candidate_is_fresh",
        ],
    }
    if not validate_policy_approval(
        baseline=approved,
        policy=policy,
        policy_sha256=_sha256(policy_raw),
    ):
        raise BaselineApprovalError("generated_policy_approval_invalid")
    return approved


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        approved = promote_candidate(
            root=args.root,
            candidate_path=args.candidate,
            profile_path=args.profile,
            policy_path=args.policy,
        )
        write_json_atomic(args.output, approved)
    except (BaselineApprovalError, OSError) as exc:
        print(f"baseline_approval_failed:{exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": approved["approval_status"],
                "approved_by": approved["approved_by"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
