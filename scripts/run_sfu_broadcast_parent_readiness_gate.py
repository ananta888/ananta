#!/usr/bin/env python3
"""Fail-closed parent-readiness gate for SFU broadcast readiness.

The gate verifies parent program evidence and declared cross-track prerequisites.
The result is content-free and fail-closed: no parent claim can be converted to
pass without matching evidence and configured rollout conditions.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHILD_TODO = ROOT / "todos/todo.webrtc-sfu-broadcast-fanout.json"
DEFAULT_PARENT_TODO = ROOT / "todos/todo.ai-snake-semantic-media-speech-program.json"
DEFAULT_PARENT_EVIDENCE = ROOT / "artifacts/test-gates/semantic-media-program-evidence.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-parent-readiness.json"
ACTIVE_STAGES = frozenset({"single_pair_opt_in", "trusted_small_group", "bounded_pilot", "general_opt_in"})
PARENT_GATE_ID = "SFB-BASE-003"


def _safe_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"parent_readiness_input_unavailable:{path}") from exc


def _extract_parent_prerequisites(child_todo: Mapping[str, Any]) -> list[str]:
    tasks = child_todo.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("parent_task_inventory_invalid")

    base_task = next((row for row in tasks if isinstance(row, Mapping) and row.get("id") == PARENT_GATE_ID), None)
    if not isinstance(base_task, Mapping):
        raise ValueError("parent_base_task_missing")
    depends_on = base_task.get("depends_on")
    if not isinstance(depends_on, list) or not depends_on:
        raise ValueError("parent_base_task_dependencies_missing")

    refs: list[str] = []
    for dep in depends_on:
        if not isinstance(dep, str) or ":" not in dep:
            continue
        source, task_id = dep.split(":", 1)
        if source != DEFAULT_PARENT_TODO.as_posix() and source != "todo.ai-snake-semantic-media-speech-program.json":
            continue
        task_id = task_id.strip()
        if task_id:
            refs.append(task_id)
    return sorted(set(refs))


def _required_parent_task_map(parent_evidence: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_tasks = parent_evidence.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("parent_task_projection_invalid")

    task_map: dict[str, dict[str, Any]] = {}
    for row in raw_tasks:
        if not isinstance(row, Mapping):
            raise ValueError("parent_task_projection_invalid")
        task_id = str(row.get("id") or "")
        if not task_id:
            raise ValueError("parent_task_id_missing")
        if task_id in task_map:
            raise ValueError("parent_task_projection_duplicate")
        if set(row) != {"id", "status", "reason_codes", "evidence_sha256"}:
            raise ValueError("parent_task_projection_shape_invalid")
        task_map[task_id] = dict(row)
    return task_map


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def evaluate_parent_readiness(
    *,
    parent_todo: Mapping[str, Any],
    parent_evidence: Mapping[str, Any],
    child_todo: Mapping[str, Any],
    evidence_profile: str,
    max_staleness_days: int,
    output_parent_artifact_path: Path,
    parent_artifact_timestamp: float,
) -> GateEvidence:
    reasons: list[str] = []

    if not evidence_profile:
        reasons.append("parent_evidence_profile_missing")
    if max_staleness_days < 1 or max_staleness_days > 365:
        reasons.append("parent_readiness_staleness_window_invalid")

    prerequisite_categories = child_todo.get("activation_prerequisite_categories", [])
    if not isinstance(prerequisite_categories, list):
        reasons.append("activation_prerequisite_categories_invalid")
        prerequisite_categories = []
    required_parent_tasks = _extract_parent_prerequisites(child_todo)
    if not required_parent_tasks:
        reasons.append("parent_prerequisite_task_reference_missing")

    if sorted(prerequisite_categories) != sorted(set(prerequisite_categories)):
        reasons.append("activation_prerequisite_categories_not_unique")

    if not isinstance(parent_todo.get("tasks"), list):
        reasons.append("parent_todo_task_projection_invalid")

    if not isinstance(parent_evidence, Mapping):
        reasons.append("parent_evidence_projection_invalid")
        parent_tasks_by_id: dict[str, dict[str, Any]] = {}
    else:
        for field in ("schema", "decision", "rollout_stage", "source_sha256", "config_sha256", "tasks", "gates", "milestones", "reason_codes"):
            if field not in parent_evidence:
                reasons.append("parent_evidence_field_missing")
                break

        if parent_evidence.get("schema") != "ananta.semantic-media-program-release-evidence.v1":
            reasons.append("parent_evidence_schema_invalid")

        if parent_evidence.get("decision") != "go":
            reasons.append("parent_no_go")
        if parent_evidence.get("rollout_stage") == "observe_only":
            reasons.append("parent_rollout_observe_only")
        if parent_evidence.get("rollout_stage") not in ACTIVE_STAGES | {"observe_only"}:
            reasons.append("parent_rollout_stage_invalid")
        if not _hex64(parent_evidence.get("source_sha256")):
            reasons.append("parent_evidence_source_digest_invalid")
        if not _hex64(parent_evidence.get("config_sha256")):
            reasons.append("parent_evidence_config_digest_invalid")

        if evidence_profile and evidence_profile.lower() == "attested":
            signature = parent_evidence.get("signature")
            key_id = parent_evidence.get("signature_key_id")
            if not isinstance(signature, str) or not signature:
                reasons.append("parent_evidence_signature_missing")
            if not isinstance(key_id, str) or not key_id:
                reasons.append("parent_evidence_signature_key_id_missing")

        parent_tasks_by_id = _required_parent_task_map(parent_evidence)

    if required_parent_tasks and isinstance(parent_tasks_by_id, dict):
        expected_count = len(required_parent_tasks)
        observed_count = len([task_id for task_id in required_parent_tasks if task_id in parent_tasks_by_id])
        if observed_count != expected_count:
            reasons.append("parent_prerequisite_task_id_mismatch")

        for task_id in required_parent_tasks:
            task = parent_tasks_by_id.get(task_id)
            if task is None:
                reasons.append(f"parent_prerequisite_task_missing:{task_id}")
                continue
            if task.get("status") != "passed":
                reasons.append(f"parent_prerequisite_task_not_passed:{task_id}")
            evidence_sha = task.get("evidence_sha256")
            if not _hex64(evidence_sha):
                reasons.append(f"parent_prerequisite_task_evidence_digest_invalid:{task_id}")

    try:
        age = datetime.fromtimestamp(parent_artifact_timestamp, tz=UTC) - datetime.now(tz=UTC)
    except (OSError, OverflowError, ValueError):
        reasons.append("parent_artifact_timestamp_invalid")
    else:
        if age > timedelta(days=max_staleness_days):
            reasons.append("parent_evidence_stale")
        if age.total_seconds() < -600:
            reasons.append("parent_evidence_time_skew")

    reasons = sorted(set(reasons))
    if evidence_profile != "default" and parent_evidence.get("evidence_profile") != evidence_profile:
        reasons.append("parent_evidence_profile_mismatch")

    source_digest = source_hash(
        ROOT,
        (
            "todos/todo.webrtc-sfu-broadcast-fanout.json",
            "todos/todo.ai-snake-semantic-media-speech-program.json",
            "artifacts/test-gates/semantic-media-program-evidence.json",
            "scripts/run_sfu_broadcast_parent_readiness_gate.py",
            "agent/services/semantic_media_program_evidence.py",
        ),
    )

    config_digest = canonical_sha256(
        {
            "evidence_profile": evidence_profile,
            "max_staleness_days": int(max_staleness_days),
            "required_parent_tasks": required_parent_tasks,
            "parent_rollout_stage": str(parent_evidence.get("rollout_stage") or ""),
            "artifact_path": _safe_path(output_parent_artifact_path),
            "prerequisite_categories": prerequisite_categories,
        }
    )

    if reasons:
        return GateEvidence(
            gate_id=PARENT_GATE_ID,
            status="failed",
            reason_codes=tuple(reasons),
            source_sha256=source_digest,
            config_sha256=config_digest,
            measurements={
                "parent_decision": str(parent_evidence.get("decision") or "missing"),
                "parent_rollout_stage": str(parent_evidence.get("rollout_stage") or "missing"),
                "parent_task_count": len(parent_todo.get("tasks") or []),
                "parent_task_prerequisite_count": len(required_parent_tasks),
            },
        )

    return GateEvidence(
        gate_id=PARENT_GATE_ID,
        status="passed",
        reason_codes=tuple(reasons),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements={
            "parent_decision": "go",
            "parent_rollout_stage": str(parent_evidence.get("rollout_stage") or ""),
            "parent_task_count": len(parent_todo.get("tasks") or []),
            "parent_task_prerequisite_count": len(required_parent_tasks),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate parent readiness for SFU broadcast track.")
    parser.add_argument("--child-todo", type=Path, default=DEFAULT_CHILD_TODO)
    parser.add_argument("--parent-todo", type=Path, default=DEFAULT_PARENT_TODO)
    parser.add_argument("--parent-evidence", type=Path, default=DEFAULT_PARENT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--staleness-days", type=int, default=14)
    parser.add_argument("--evidence-profile", default="default")
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        child_todo = _load_json(args.child_todo)
        parent_todo = _load_json(args.parent_todo)
        parent_evidence = _load_json(args.parent_evidence)
        evidence = evaluate_parent_readiness(
            parent_todo=parent_todo,
            parent_evidence=parent_evidence,
            child_todo=child_todo,
            evidence_profile=args.evidence_profile,
            max_staleness_days=int(args.staleness_days),
            output_parent_artifact_path=args.output,
            parent_artifact_timestamp=Path(args.parent_evidence).stat().st_mtime,
        )
    except (ValueError, OSError) as exc:
        evidence = unavailable_evidence(
            PARENT_GATE_ID,
            source_sha256=canonical_sha256({"missing_input": str(exc)}),
            config_sha256=canonical_sha256({"evidence_profile": args.evidence_profile}),
            reason_code="parent_readiness_input_invalid",
        )

    if not args.no_write:
        from agent.services.semantic_media_program_evidence import write_report

        write_report(args.output, evidence)
        print(json.dumps({"output": str(_safe_path(args.output)), "status": evidence.status}, sort_keys=True))

    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
