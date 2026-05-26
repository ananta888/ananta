from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.planning_track_contract_service import (
    planning_contract_hash,
    planning_contract_ref,
    planning_track_profile,
    unwrap_planning_track_payload,
    validate_planning_track_payload,
)
from agent.services.planning_utils import extract_json_payload

_PROMPT_TEMPLATE_FILE = Path(__file__).resolve().parents[2] / "prompts" / "planning" / "track_planning.j2"


def planner_role_spec(*, mode: str = "track_planner") -> dict[str, Any]:
    profile = planning_track_profile(mode=mode)
    return {
        "role": profile["mode"],
        "planner_mode": profile["mode"],
        "supports_track_output": True,
        "allows_code_changes": False,
        "output_kind": "planning_track",
        "output_schema_ref": planning_contract_ref()["schema_ref"],
    }


def _normalize_source_ref(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("source_ref") or value.get("artifact_ref") or value.get("id") or "").strip()
    return str(value or "").strip()


def build_planner_context_envelope(
    *,
    goal_id: str,
    goal_text: str,
    constraints: list[str] | None,
    available_artifacts: list[dict[str, Any]] | None,
    allowed_source_refs: list[str] | None,
    codecompass_refs: list[str] | None = None,
) -> dict[str, Any]:
    allowed = {str(item).strip() for item in list(allowed_source_refs or []) if str(item).strip()}
    accepted_artifacts: list[dict[str, Any]] = []
    denied_refs: list[str] = []
    for item in list(available_artifacts or []):
        if not isinstance(item, dict):
            continue
        source_ref = _normalize_source_ref(item)
        if source_ref and source_ref not in allowed:
            denied_refs.append(source_ref)
            continue
        accepted_artifacts.append(dict(item))
    artifact_source_refs = [
        _normalize_source_ref(item)
        for item in accepted_artifacts
        if _normalize_source_ref(item)
    ]
    context_summary = {
        "artifact_count": len(accepted_artifacts),
        "source_refs": artifact_source_refs[:20],
        "truncated": len(artifact_source_refs) > 20,
    }
    return {
        "goal_id": str(goal_id or "").strip(),
        "goal_text": str(goal_text or "").strip(),
        "constraints": [str(item).strip() for item in list(constraints or []) if str(item).strip()],
        "available_artifacts": accepted_artifacts,
        "allowed_source_refs": sorted(allowed),
        "denied_source_refs": sorted(set(denied_refs)),
        "codecompass_refs": [str(item).strip() for item in list(codecompass_refs or []) if str(item).strip()],
        "context_summary": context_summary,
        "schema_ref": planning_contract_ref()["schema_ref"],
        "schema_hash": planning_contract_hash(),
    }


def load_track_planning_prompt_template() -> str:
    return _PROMPT_TEMPLATE_FILE.read_text(encoding="utf-8")


def render_track_planning_prompt(*, goal_text: str, context_envelope: dict[str, Any]) -> str:
    template = load_track_planning_prompt_template()
    return template.format(
        goal_text=str(goal_text or "").strip(),
        constraints_json=json.dumps(list(context_envelope.get("constraints") or []), ensure_ascii=False, indent=2),
        context_json=json.dumps(context_envelope, ensure_ascii=False, indent=2),
        required_fields_json=json.dumps(
            planning_track_profile(mode="track_planner")["minimum_task_quality"]["required_fields"],
            ensure_ascii=False,
        ),
        schema_ref=planning_contract_ref()["schema_ref"],
    )


def parse_planner_output(raw_output: str) -> dict[str, Any]:
    parsed_json = extract_json_payload(str(raw_output or ""))
    if not parsed_json:
        return {"status": "invalid", "reason_code": "non_json_output", "errors": ["No JSON payload found."]}
    try:
        candidate = json.loads(parsed_json)
    except json.JSONDecodeError:
        return {"status": "invalid", "reason_code": "invalid_json", "errors": ["JSON decode failed."]}
    if not isinstance(candidate, dict):
        return {"status": "invalid", "reason_code": "invalid_shape", "errors": ["Planning output must be a JSON object."]}
    payload, envelope = unwrap_planning_track_payload(candidate)
    errors = validate_planning_track_payload(payload)
    if errors:
        return {
            "status": "invalid",
            "reason_code": "schema_validation_failed",
            "errors": errors,
            "payload": payload,
            "envelope": envelope,
        }
    return {"status": "valid", "reason_code": "ok", "errors": [], "payload": payload, "envelope": envelope}
