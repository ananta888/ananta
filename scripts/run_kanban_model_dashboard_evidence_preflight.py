#!/usr/bin/env python3
"""Read-only readiness check for Kanban model-dashboard release evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from scripts.run_kanban_model_dashboard_evidence import REQUIRED_SUITES, SUITE_SPECS
else:
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


def _approved_baseline(document: Mapping[str, Any] | None) -> bool:
    if document is None:
        return False
    approved_at = document.get("approved_at")
    if (
        document.get("schema") != PERFORMANCE_BASELINE_SCHEMA
        or document.get("approval_status") != "approved"
        or not isinstance(document.get("approved_by"), str)
        or not document["approved_by"].strip()
        or not isinstance(approved_at, str)
    ):
        return False
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


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
    gate = _read_json(root, PERFORMANCE_GATE_RELATIVE)
    contract = _orchestrator_contract()

    uncommitted_candidate = worktree_dirty or not bool(
        contract["required_suite_count"] == 7
        and contract["configured_suite_count"] == 7
        and contract["performance_approved_baseline_only"]
    )
    baseline_approval_required = not _approved_baseline(approved)

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
                "category": "operational",
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
                "present_and_approved": _approved_baseline(approved),
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
