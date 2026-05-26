from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.planning_track_contract_service import (
    planning_contract_hash,
    planning_contract_ref,
    unwrap_planning_track_payload,
)
from agent.services.planning_utils import extract_json_payload

_ROOT = Path(__file__).resolve().parents[2]
_TRACK_SCHEMA = _ROOT / "todos" / "todo.track.schema.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _summary_count(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        result[token] = int(result.get(token, 0)) + 1
    return result


def load_track_schema(*, schema_ref: str | None = None, schema_store: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    ref = str(schema_ref or planning_contract_ref()["schema_ref"]).strip()
    if schema_store and ref in schema_store:
        return dict(schema_store[ref])
    candidate = (_ROOT / ref).resolve()
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    if _TRACK_SCHEMA.exists():
        return json.loads(_TRACK_SCHEMA.read_text(encoding="utf-8"))
    raise ValueError(f"planning_track_schema_not_found:{ref}")


def validate_planning_track_with_details(
    payload: dict[str, Any],
    *,
    schema_ref: str | None = None,
    schema_store: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    schema = load_track_schema(schema_ref=schema_ref, schema_store=schema_store)
    validator = Draft202012Validator(schema)
    issues: list[dict[str, str]] = []
    for error in sorted(validator.iter_errors(payload), key=lambda err: list(err.path)):
        path = "/".join(map(str, error.path)) or "$"
        if error.validator == "required":
            reason_code = "missing_required_field"
        elif error.validator == "minItems" and path.endswith("acceptance_criteria"):
            reason_code = "empty_acceptance_criteria"
        else:
            reason_code = "schema_validation_error"
        issues.append(
            {
                "path": path,
                "reason_code": reason_code,
                "human_message": str(error.message),
            }
        )
    return issues


def compute_tasks_status_summary(payload: dict[str, Any]) -> dict[str, Any]:
    tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
    milestones = [dict(item) for item in list(payload.get("milestones") or []) if isinstance(item, dict)]
    statuses = [str(item.get("status") or "").strip() for item in tasks]
    by_status = {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}
    for status in statuses:
        if status in by_status:
            by_status[status] += 1
    by_priority = _summary_count([str(item.get("priority") or "").strip() for item in tasks])
    by_risk = _summary_count([str(item.get("risk") or "").strip() for item in tasks])
    total = len(tasks)
    done = by_status["done"]
    progress = round((done / total) * 100, 1) if total else 0.0

    critical_task_ids = [str(item).strip() for item in list(payload.get("critical_path_tasks") or []) if str(item).strip()]
    task_by_id = {str(item.get("id") or "").strip(): item for item in tasks}
    critical_done = 0
    for task_id in critical_task_ids:
        task = task_by_id.get(task_id)
        if isinstance(task, dict) and str(task.get("status") or "").strip() == "done":
            critical_done += 1

    milestone_statuses = [str(item.get("status") or "").strip() for item in milestones]
    milestone_summary = {"total": len(milestones), "todo": 0, "in_progress": 0, "blocked": 0, "done": 0}
    for status in milestone_statuses:
        if status in milestone_summary:
            milestone_summary[status] += 1

    return {
        "total": total,
        "by_status": by_status,
        "progress_percent_done": progress,
        "by_priority": by_priority,
        "by_risk": by_risk,
        "critical_path": {
            "total": len(critical_task_ids),
            "done": critical_done,
            "remaining": max(0, len(critical_task_ids) - critical_done),
        },
        "milestones": milestone_summary,
    }


def validate_summary_consistency(payload: dict[str, Any], *, repair_mode: bool = False) -> dict[str, Any]:
    candidate = dict(payload)
    provided = dict(candidate.get("tasks_status_summary") or {})
    computed = compute_tasks_status_summary(candidate)
    issues: list[dict[str, str]] = []

    def _append(reason_code: str, message: str) -> None:
        issues.append({"path": "tasks_status_summary", "reason_code": reason_code, "human_message": message})

    if provided.get("total") != computed.get("total"):
        _append("summary_total_mismatch", "tasks_status_summary.total does not match tasks length.")
    if dict(provided.get("by_priority") or {}) != dict(computed.get("by_priority") or {}):
        _append("summary_by_priority_mismatch", "tasks_status_summary.by_priority is inconsistent with tasks.")
    if dict(provided.get("by_risk") or {}) != dict(computed.get("by_risk") or {}):
        _append("summary_by_risk_mismatch", "tasks_status_summary.by_risk is inconsistent with tasks.")
    if dict(provided.get("by_status") or {}) != dict(computed.get("by_status") or {}):
        _append("summary_by_status_mismatch", "tasks_status_summary.by_status is inconsistent with tasks.")
    if dict(provided.get("critical_path") or {}) != dict(computed.get("critical_path") or {}):
        _append("summary_critical_path_mismatch", "tasks_status_summary.critical_path is inconsistent with tasks.")

    if issues and repair_mode:
        candidate["tasks_status_summary"] = computed
        return {"valid": True, "issues": issues, "repaired_payload": candidate, "repaired": True}
    return {"valid": not issues, "issues": issues, "repaired_payload": candidate, "repaired": False}


def build_planning_repair_prompt(*, raw_output: str, issues: list[dict[str, str]]) -> str:
    issue_lines = "\n".join(
        f"- path={item.get('path')} reason_code={item.get('reason_code')} message={item.get('human_message')}"
        for item in list(issues or [])
    ) or "- none"
    return (
        "Repair this planning track output.\n"
        "Return JSON only. No markdown fences.\n"
        "Keep only the track payload or envelope(kind=planning_track,payload).\n"
        "Use these validation issues as strict guidance:\n"
        f"{issue_lines}\n\n"
        "Original output:\n"
        f"{str(raw_output or '')}"
    )


def repair_planning_output_once(
    *,
    raw_output: str,
    issues: list[dict[str, str]],
    repair_fn: Callable[[str], str] | None,
) -> dict[str, Any]:
    if repair_fn is None:
        return {"repaired": False, "raw_output": raw_output, "repair_attempt_count": 0}
    prompt = build_planning_repair_prompt(raw_output=raw_output, issues=issues)
    repaired = str(repair_fn(prompt) or "")
    return {"repaired": True, "raw_output": repaired, "repair_attempt_count": 1, "repair_prompt": prompt}


def persist_planning_track_result(
    *,
    goal_id: str,
    task_id: str,
    worker_id: str,
    raw_output: str,
    prompt_template_ref: str,
    final_prompt: str,
    model_ref: dict[str, str],
    config_refs: dict[str, str],
    available_artifacts: list[dict[str, Any]] | None = None,
    repair_fn: Callable[[str], str] | None = None,
    goal_artifact_service: GoalArtifactService | None = None,
    schema_store: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    service = goal_artifact_service or GoalArtifactService()
    artifact_refs = [str(item.get("source_ref") or item.get("artifact_ref") or "").strip() for item in list(available_artifacts or []) if isinstance(item, dict)]
    context_hash = _stable_hash({"goal_id": goal_id, "artifact_refs": sorted([ref for ref in artifact_refs if ref])})
    usage_tracking = service.validate_and_record_context_usages(
        goal_id=goal_id,
        artifact_refs=artifact_refs,
        task_id=task_id,
        worker_id=worker_id,
        context_hash=context_hash,
    )

    parsed_json = extract_json_payload(str(raw_output or ""))
    parse_errors: list[dict[str, str]] = []
    candidate: dict[str, Any] | None = None
    if not parsed_json:
        parse_errors.append({"path": "$", "reason_code": "non_json_output", "human_message": "No JSON payload found."})
    else:
        try:
            loaded = json.loads(parsed_json)
            if isinstance(loaded, dict):
                candidate = loaded
            else:
                parse_errors.append({"path": "$", "reason_code": "invalid_shape", "human_message": "Planning output must be a JSON object."})
        except json.JSONDecodeError as exc:
            parse_errors.append({"path": "$", "reason_code": "invalid_json", "human_message": str(exc)})

    repair_attempt_count = 0
    if parse_errors:
        repair_result = repair_planning_output_once(raw_output=raw_output, issues=parse_errors, repair_fn=repair_fn)
        repair_attempt_count = int(repair_result.get("repair_attempt_count") or 0)
        if repair_result.get("repaired"):
            raw_output = str(repair_result.get("raw_output") or "")
            parsed_json = extract_json_payload(raw_output)
            parse_errors = []
            if not parsed_json:
                parse_errors.append({"path": "$", "reason_code": "non_json_output", "human_message": "No JSON payload found after repair."})
            else:
                try:
                    loaded = json.loads(parsed_json)
                    if isinstance(loaded, dict):
                        candidate = loaded
                    else:
                        parse_errors.append({"path": "$", "reason_code": "invalid_shape", "human_message": "Planning output must be a JSON object."})
                except json.JSONDecodeError as exc:
                    parse_errors.append({"path": "$", "reason_code": "invalid_json", "human_message": str(exc)})

    validation_issues: list[dict[str, str]] = []
    payload: dict[str, Any] = {}
    envelope: dict[str, Any] | None = None
    if not parse_errors and isinstance(candidate, dict):
        payload, envelope = unwrap_planning_track_payload(candidate)
        validation_issues = validate_planning_track_with_details(payload, schema_store=schema_store)
        summary_result = validate_summary_consistency(payload, repair_mode=True)
        if summary_result.get("repaired"):
            payload = dict(summary_result.get("repaired_payload") or payload)
        validation_issues.extend(list(summary_result.get("issues") or []))

    all_issues = parse_errors + validation_issues
    valid = not (parse_errors or validation_issues)
    status = "valid" if valid else ("degraded" if repair_attempt_count > 0 else "failed")
    artifact_status = "created" if valid else "failed"
    output_id = f"out-{hashlib.sha1(f'{goal_id}:{task_id}:{raw_output}'.encode('utf-8')).hexdigest()[:14]}"
    payload_hash = _stable_hash(payload if payload else {"raw_output": raw_output})
    provenance_id = f"prov-{hashlib.sha1(f'{goal_id}:{task_id}:{payload_hash}'.encode('utf-8')).hexdigest()[:16]}"

    output_ref = output_id
    service.upsert_execution_provenance(
        goal_id=goal_id,
        provenance={
            "schema": "execution_provenance.v1",
            "provenance_id": provenance_id,
            "goal_id": goal_id,
            "task_id": task_id,
            "execution_id": f"exec-{hashlib.sha1(f'{goal_id}:{task_id}:planning-track'.encode('utf-8')).hexdigest()[:14]}",
            "worker_id": worker_id,
            "worker_kind": "planner",
            "runtime_target_ref": {"runtime_type": "ananta-worker", "location": "local"},
            "model_ref": {
                "provider_id": str(model_ref.get("provider_id") or "unknown"),
                "model_id": str(model_ref.get("model_id") or "unknown"),
            },
            "config_refs": {
                "worker_config_ref": str(config_refs.get("worker_config_ref") or "cfg:worker"),
                "runtime_config_ref": str(config_refs.get("runtime_config_ref") or "cfg:runtime"),
                "model_config_ref": str(config_refs.get("model_config_ref") or "cfg:model"),
                "policy_config_ref": str(config_refs.get("policy_config_ref") or "cfg:policy"),
            },
            "prompt_refs": {
                "prompt_template_ref": prompt_template_ref,
                "final_prompt_hash": _stable_hash({"final_prompt": final_prompt}),
            },
            "input_usage_refs": list(usage_tracking.get("source_usage_refs") or []),
            "output_artifact_refs": [output_ref],
            "created_at": _now_iso(),
            "extensions": {
                "schema_ref": planning_contract_ref()["schema_ref"],
                "schema_hash": planning_contract_hash(),
            },
        },
    )
    output_artifact = service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": output_id,
            "goal_id": goal_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "artifact_type": "planning_track",
            "created_at": _now_iso(),
            "input_usage_refs": list(usage_tracking.get("source_usage_refs") or []),
            "artifact_ref": f"planning_track:{output_id}",
            "content_hash": payload_hash,
            "status": artifact_status,
            "provenance_id": provenance_id,
            "provenance_summary": f"planning_track status={status}; issues={len(all_issues)}",
            "verification_status": status,
            "extensions": {
                "active_plan_candidate": bool(valid),
                "validation_issues": all_issues[:20],
                "schema_ref": planning_contract_ref()["schema_ref"],
                "schema_hash": planning_contract_hash(),
                "repair_attempt_count": repair_attempt_count,
                "payload": payload if valid else None,
                "envelope": envelope if isinstance(envelope, dict) else None,
            },
        },
    )

    return {
        "status": status,
        "valid": valid,
        "repair_attempt_count": repair_attempt_count,
        "issues": all_issues,
        "output_artifact": output_artifact,
        "provenance_id": provenance_id,
        "source_usage_refs": usage_tracking.get("source_usage_refs") or [],
        "denied_context_refs": usage_tracking.get("denied_context_refs") or [],
    }

