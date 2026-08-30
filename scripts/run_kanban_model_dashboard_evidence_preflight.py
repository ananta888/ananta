#!/usr/bin/env python3
"""Read-only readiness check for Kanban model-dashboard release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from scripts.performance.kanban_baseline_approval_policy import (
        protected_candidate_sha256,
        validate_policy_approval,
    )
    from scripts.run_kanban_model_dashboard_evidence import REQUIRED_SUITES, SUITE_SPECS
else:
    from performance.kanban_baseline_approval_policy import (
        protected_candidate_sha256,
        validate_policy_approval,
    )
    from run_kanban_model_dashboard_evidence import REQUIRED_SUITES, SUITE_SPECS


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ananta.kanban-model-dashboard.evidence-preflight.v1"
PERFORMANCE_BASELINE_SCHEMA = "ananta.kanban-model-dashboard.performance-baseline.v1"
PERFORMANCE_GATE_SCHEMA = "ananta.kanban-model-dashboard.performance-gate.v1"
PERFORMANCE_SUITE_ID = "kanban-model-dashboard.performance.v1"
BASELINE_CANDIDATE_RELATIVE = Path(
    "artifacts/test-gates/kanban-model-dashboard-performance-baseline-candidate.v1.json"
)
APPROVED_BASELINE_RELATIVE = Path(
    "config/test-profiles/kanban-model-dashboard/baselines/"
    "formal-performance-approved.v1.json"
)
PERFORMANCE_GATE_RELATIVE = Path(
    "artifacts/test-gates/kanban-model-dashboard-performance-gate.v1.json"
)
APPROVAL_POLICY_RELATIVE = Path(
    "config/test-profiles/kanban-model-dashboard/baseline-approval-policy.v1.json"
)
FORMAL_PROFILE_RELATIVE = Path(
    "config/test-profiles/kanban-model-dashboard/formal-performance.v1.json"
)
MAX_JSON_BYTES = 4 * 1024 * 1024

GitStateReader = Callable[[Path], tuple[str | None, bool, str | None]]


def _read_json(root: Path, relative: Path) -> Mapping[str, Any] | None:
    path = root / relative
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        if resolved.stat().st_size > MAX_JSON_BYTES:
            return None
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _read_git_state(root: Path) -> tuple[str | None, bool, str | None]:
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
        return None, True, f"{type(exc).__name__}: {exc}"
    return head or None, bool(status.strip()), None


def _file_sha256(root: Path, relative: Path) -> str | None:
    path = root / relative
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        raw = resolved.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_JSON_BYTES:
        return None
    return hashlib.sha256(raw).hexdigest()


def _approved_baseline(
    document: Mapping[str, Any] | None,
    *,
    candidate: Mapping[str, Any] | None,
    candidate_sha256: str | None,
    policy: Mapping[str, Any] | None,
    policy_sha256: str | None,
    profile_sha256: str | None,
) -> bool:
    if (
        document is None
        or candidate is None
        or candidate_sha256 is None
        or policy is None
        or policy_sha256 is None
        or profile_sha256 is None
    ):
        return False
    approved_at = document.get("approved_at")
    if (
        document.get("schema") != PERFORMANCE_BASELINE_SCHEMA
        or document.get("approval_status") != "approved"
        or not isinstance(approved_at, str)
    ):
        return False
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    approval = document.get("approval")
    profile = document.get("profile")
    return bool(
        parsed.tzinfo is not None
        and isinstance(approval, Mapping)
        and approval.get("candidate_sha256") == candidate_sha256
        and protected_candidate_sha256(document)
        == protected_candidate_sha256(candidate)
        and isinstance(profile, Mapping)
        and profile.get("sha256") == profile_sha256
        and validate_policy_approval(
            baseline=document,
            policy=policy,
            policy_sha256=policy_sha256,
        )
    )


def _blocked_gate_contract(document: Mapping[str, Any] | None) -> bool:
    return bool(
        document is not None
        and document.get("schema") == PERFORMANCE_GATE_SCHEMA
        and document.get("suite_id") == PERFORMANCE_SUITE_ID
        and document.get("status") == "blocked"
        and document.get("release_evidence") is False
        and document.get("formal_gate_eligible") is False
        and document.get("blockers") == [{"code": "baseline_approval_required"}]
    )


def _serialize_spec(spec: object) -> str:
    value = asdict(spec) if is_dataclass(spec) else repr(spec)
    return json.dumps(value, sort_keys=True, default=str)


def _orchestrator_contract() -> Mapping[str, Any]:
    serialized_specs = [_serialize_spec(spec) for spec in SUITE_SPECS.values()]
    performance_specs = [
        value
        for value in serialized_specs
        if "run_kanban_model_dashboard_performance_suite.py" in value
    ]
    approved_path = APPROVED_BASELINE_RELATIVE.as_posix()
    candidate_path = BASELINE_CANDIDATE_RELATIVE.as_posix()
    approved_only = bool(
        len(performance_specs) == 1
        and approved_path in performance_specs[0]
        and candidate_path not in performance_specs[0]
    )
    return {
        "required_suite_count": len(REQUIRED_SUITES),
        "required_suites": list(REQUIRED_SUITES),
        "configured_suite_count": len(SUITE_SPECS),
        "performance_approved_baseline_only": approved_only,
    }


def run_preflight(
    *,
    root: Path = ROOT,
    git_state_reader: GitStateReader = _read_git_state,
) -> Mapping[str, Any]:
    """Inspect readiness without executing suites or creating evidence files."""

    root = root.resolve(strict=True)
    head_sha, worktree_dirty, git_error = git_state_reader(root)
    candidate = _read_json(root, BASELINE_CANDIDATE_RELATIVE)
    approved = _read_json(root, APPROVED_BASELINE_RELATIVE)
    policy = _read_json(root, APPROVAL_POLICY_RELATIVE)
    gate = _read_json(root, PERFORMANCE_GATE_RELATIVE)
    candidate_sha256 = _file_sha256(root, BASELINE_CANDIDATE_RELATIVE)
    policy_sha256 = _file_sha256(root, APPROVAL_POLICY_RELATIVE)
    profile_sha256 = _file_sha256(root, FORMAL_PROFILE_RELATIVE)
    contract = _orchestrator_contract()

    uncommitted_candidate = worktree_dirty or not bool(
        contract["required_suite_count"] == 7
        and contract["configured_suite_count"] == 7
        and contract["performance_approved_baseline_only"]
    )
    approved_baseline_valid = _approved_baseline(
        approved,
        candidate=candidate,
        candidate_sha256=candidate_sha256,
        policy=policy,
        policy_sha256=policy_sha256,
        profile_sha256=profile_sha256,
    )
    baseline_approval_required = not approved_baseline_valid

    reason_codes = []
    if uncommitted_candidate:
        reason_codes.append("uncommitted_candidate")
    if baseline_approval_required:
        reason_codes.append("baseline_approval_required")

    boundaries = []
    if uncommitted_candidate:
        boundaries.append(
            {
                "code": "uncommitted_candidate",
                "category": "technical",
            }
        )
    if baseline_approval_required:
        boundaries.append(
            {
                "code": "baseline_approval_required",
                "category": "technical_policy",
            }
        )

    return {
        "schema": SCHEMA,
        "status": "blocked" if reason_codes else "ready",
        "read_only": True,
        "commands_executed": False,
        "evidence_written": False,
        "passed_evidence_eligible": not reason_codes,
        "candidate_ref": "HEAD",
        "candidate_sha": head_sha,
        "reason_codes": reason_codes,
        "boundaries": boundaries,
        "git_inspection_error": git_error,
        "orchestrator": contract,
        "performance": {
            "baseline_candidate": {
                "path": BASELINE_CANDIDATE_RELATIVE.as_posix(),
                "approval_status": (
                    candidate.get("approval_status") if candidate is not None else None
                ),
            },
            "approved_baseline": {
                "path": APPROVED_BASELINE_RELATIVE.as_posix(),
                "present_and_approved": approved_baseline_valid,
                "approval_method": (
                    (approved.get("approval") or {}).get("method")
                    if approved is not None
                    and isinstance(approved.get("approval"), Mapping)
                    else None
                ),
            },
            "approval_policy": {
                "path": APPROVAL_POLICY_RELATIVE.as_posix(),
                "present": policy is not None,
            },
            "last_gate": {
                "path": PERFORMANCE_GATE_RELATIVE.as_posix(),
                "status": gate.get("status") if gate is not None else None,
                "blockers": gate.get("blockers") if gate is not None else None,
                "blocked_contract_valid": _blocked_gate_contract(gate),
            },
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only preflight for the seven-suite Kanban model-dashboard "
            "release-evidence producer."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to inspect (default: detected project root).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_preflight(root=args.root)
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
