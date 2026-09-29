#!/usr/bin/env python3
"""Fail-closed static release gate for the optional HRM experiment surface."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable


def _contains(root: Path, path: str, needles: Iterable[str]) -> tuple[bool, str]:
    target = root / path
    try:
        value = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False, f"missing_or_unreadable:{path}"
    missing = [needle for needle in needles if needle not in value]
    return not missing, ("ok" if not missing else f"missing:{','.join(missing)}")


def evaluate_repository(root: Path) -> list[dict[str, object]]:
    specifications = {
        "feature_default_off": (
            "agent/config.py",
            ("HRM_EXPERIMENTS_ENABLED", "default=False"),
        ),
        "hub_blueprint_registered": (
            "agent/bootstrap/routes.py",
            ("hrm_experiments_bp", "register_blueprint"),
        ),
        "service_scope_enforced": (
            "agent/services/workflow_worker_service_auth.py",
            ("hrm.experiment.execute", "HRM_EXPERIMENT_WORKER_SCOPE"),
        ),
        "worker_cannot_orchestrate": (
            "worker/hrm_experiments/task_handler.py",
            ("hrm_hub_delegation_required", '"authoritative_source": "hub"'),
        ),
        "worker_registration_is_non_orchestrating": (
            "agent/ai_agent.py",
            ("child_task_creation_forbidden", "peer_network_forbidden"),
        ),
        "capability_heartbeat": (
            "worker/hrm_experiments/heartbeat.py",
            ("HrmCapabilityHeartbeat", "advertise_capability"),
        ),
        "runner_networkless": (
            "docker-compose.hrm-experiments.yml",
            ("network_mode: none", "read_only: true", "cap_drop:", "no-new-privileges:true"),
        ),
        "runner_bounded": (
            "docker-compose.hrm-experiments.yml",
            ("pids_limit:", "mem_limit:", "cpus:", "tmpfs:"),
        ),
        "persistent_idempotency": (
            "agent/db_models/hrm_experiment_idempotency.py",
            ("HrmIdempotencyReceiptDB", "request_digest", "UniqueConstraint"),
        ),
        "unbounded_event_high_water_mark": (
            "agent/repositories/hrm_experiments.py",
            ("def last_event_sequence", "func.max(HrmRunEventDB.sequence)"),
        ),
        "closed_contracts": (
            "schemas/hrm-experiments/contracts.v1.json",
            ('"additionalProperties": false', '"run_intent"'),
        ),
        "openapi_surface": (
            "docs/contracts/hrm-experiments.openapi.yaml",
            ("/runs/{run_id}/cancel:", "/reports/{report_id}:"),
        ),
        "sudoku_reference_profile_is_explicit": (
            "worker/hrm_experiments/puzzles/sudoku.py",
            ("hrm-sudoku-reference-v1",),
        ),
        "maze_reference_profile_is_explicit": (
            "worker/hrm_experiments/puzzles/maze.py",
            ("hrm-maze-reference-v1",),
        ),
        "arc_reference_profile_is_explicit": (
            "worker/hrm_experiments/puzzles/arc.py",
            ("hrm-arc-reference-v1",),
        ),
        "public_cli": (
            "scripts/hrm_experiments_client.py",
            ("Idempotency-Key", "_NoRedirect", "https_required"),
        ),
    }
    checks: list[dict[str, object]] = []
    for check_id, (path, needles) in specifications.items():
        passed, detail = _contains(root, path, needles)
        checks.append({"check_id": check_id, "passed": passed, "detail": detail})

    forbidden: list[str] = []
    generation_patterns = (
        re.compile(r"f[\"'](?:SRC|RUN)_\{"),
        re.compile(r"[\"'](?:SRC|RUN)_[\"']\s*\+"),
    )
    targets = [
        root / "agent/routes/hrm_experiments.py",
        root / "scripts/hrm_experiments_client.py",
        *(root / "agent/services/hrm_experiments").glob("*.py"),
        *(root / "worker/hrm_experiments").rglob("*.py"),
    ]
    for path in sorted(set(targets)):
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            forbidden.append(str(path.relative_to(root)))
            continue
        if any(pattern.search(value) for pattern in generation_patterns):
            forbidden.append(str(path.relative_to(root)))
    checks.append(
        {
            "check_id": "no_invented_source_identifiers_in_runtime",
            "passed": not forbidden,
            "detail": "ok" if not forbidden else f"found:{','.join(forbidden)}",
        }
    )
    return checks


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    checks = evaluate_repository(root)
    passed = all(bool(item["passed"]) for item in checks)
    print(
        json.dumps(
            {
                "schema": "ananta.hrm-experiments.release-gate.v1",
                "passed": passed,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
