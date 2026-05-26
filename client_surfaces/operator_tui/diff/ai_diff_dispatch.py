from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.services.config_snapshot_service import ConfigSnapshotService
from agent.services.prompt_snapshot_service import PromptSnapshotService
from client_surfaces.operator_tui.diff.ai_diff_context import build_ai_diff_context_envelope
from client_surfaces.operator_tui.diff.ai_diff_prompts import render_ai_diff_prompt
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver

_RESPONSE_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "tui" / "ai_diff_response.v1.json"


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_ai_diff_response(payload: dict[str, Any]) -> list[str]:
    schema = json.loads(_RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def _mock_ai_response(*, mode: str, envelope: dict[str, Any]) -> dict[str, Any]:
    refs = [str(item.get("source_ref_id") or "") for item in list(envelope.get("diff_source_refs") or []) if isinstance(item, dict)]
    summary = f"{mode} analysis prepared for {len(refs)} source(s)"
    findings = ["Diff context analyzed"] if envelope.get("diff_summary") else ["No diff content available"]
    risks = ["Missing source grants were denied"] if envelope.get("denied_context_refs") else []
    suggested_tests = ["Run targeted unit tests for changed files"] if mode in {"review", "risk", "tests", "patch"} else []
    patch_suggestions: list[str] = []
    if mode == "patch":
        patch_suggestions = [
            "--- a/demo.txt\n+++ b/demo.txt\n@@ -1 +1 @@\n-old\n+new",
        ]
    return {
        "schema": "ai_diff_response.v1",
        "status": "success",
        "artifact_type": mode,
        "summary": summary,
        "findings": findings,
        "risks": risks,
        "suggested_tests": suggested_tests,
        "patch_suggestions": patch_suggestions,
        "source_refs": refs,
    }


def dispatch_ai_diff_request(
    *,
    goal_id: str | None,
    diff3_state: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    service = GoalArtifactService()
    resolver = DiffSourceResolver(repo_root=Path.cwd(), goal_artifact_service=service)
    context_envelope = build_ai_diff_context_envelope(
        diff3_state=diff3_state,
        goal_id=goal_id,
        resolver=resolver,
        goal_artifact_service=service,
        max_context_chars=3000,
    )
    prompt_service = PromptSnapshotService()
    config_service = ConfigSnapshotService()
    template_text = (
        "CONTROL: strict JSON\n"
        "TASK: analyze diff\n"
        "DIFF_CONTEXT: {{context}}\n"
        "CODECOMPASS_CONTEXT: none\n"
        "OUTPUT_SCHEMA: ai_diff_response.v1\n"
    )
    template_ref = f"prompt:diff3/{mode}"
    template_snapshot = prompt_service.build_template_snapshot(
        prompt_template_ref=template_ref,
        template_path=f"prompts/diff3/{mode}.tmpl",
        template_version="v1",
        template_text=template_text,
        renderer="native",
        expected_output_schema_ref="schemas/tui/ai_diff_response.v1.json",
    )
    rendered_prompt = render_ai_diff_prompt(mode=mode, context_envelope=context_envelope)
    final_prompt = prompt_service.build_final_prompt_record(
        prompt_template_ref=template_ref,
        variables_payload={"goal_id": goal_id, "mode": mode},
        final_prompt_text=rendered_prompt,
        context_hash=hashlib.sha256(json.dumps(context_envelope, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        input_usage_refs=[],
        output_schema_ref="schemas/tui/ai_diff_response.v1.json",
        store_raw_prompt=False,
    )
    response = _mock_ai_response(mode=mode, envelope=context_envelope)
    errors = _validate_ai_diff_response(response)
    if errors:
        return {
            "status": "degraded",
            "reason_code": "invalid_ai_diff_response",
            "errors": errors,
            "response": {
                "schema": "ai_diff_response.v1",
                "status": "degraded",
                "artifact_type": mode,
                "summary": "AI response validation failed",
                "findings": [],
                "risks": [],
                "suggested_tests": [],
                "patch_suggestions": [],
                "source_refs": [],
                "reason_code": "invalid_ai_diff_response",
            },
            "context_envelope": context_envelope,
        }

    worker_cfg = config_service.build_snapshot(
        config_kind="worker_config",
        source_path_or_ref="config/tui-diff3",
        scope=f"goal:{goal_id or 'none'}",
        config_payload={"worker_kind": "tui_ai_diff"},
    )
    runtime_cfg = config_service.build_snapshot(
        config_kind="runtime_config",
        source_path_or_ref="config/runtime/local",
        scope=f"goal:{goal_id or 'none'}",
        config_payload={"runtime_type": "local_python"},
    )
    model_cfg = config_service.build_snapshot(
        config_kind="model_config",
        source_path_or_ref="config/model/mock",
        scope=f"goal:{goal_id or 'none'}",
        config_payload={"provider_id": "mock", "model_id": "diff3-mock"},
    )
    policy_cfg = config_service.build_snapshot(
        config_kind="policy_config",
        source_path_or_ref="config/policy/default",
        scope=f"goal:{goal_id or 'none'}",
        config_payload={"raw_prompt_access": "blocked"},
    )

    response_hash = hashlib.sha256(json.dumps(response, sort_keys=True).encode("utf-8")).hexdigest()
    output_artifact_id = f"out-diff3-{response_hash[:12]}"
    provenance_id = f"prov-diff3-{response_hash[:12]}"
    execution_id = f"exec-diff3-{response_hash[:12]}"

    if goal_id:
        provenance_payload = {
            "schema": "execution_provenance.v1",
            "provenance_id": provenance_id,
            "goal_id": goal_id,
            "task_id": f"diff3-{mode}",
            "execution_id": execution_id,
            "worker_id": "operator-tui-ai-diff",
            "worker_kind": "tui",
            "runtime_target_ref": {"runtime_type": "local_python", "location": "local"},
            "model_ref": {"provider_id": "mock", "model_id": "diff3-mock"},
            "config_refs": {
                "worker_config_ref": worker_cfg["config_snapshot_id"],
                "runtime_config_ref": runtime_cfg["config_snapshot_id"],
                "model_config_ref": model_cfg["config_snapshot_id"],
                "policy_config_ref": policy_cfg["config_snapshot_id"],
            },
            "prompt_refs": {
                "prompt_template_ref": template_snapshot["prompt_template_ref"],
                "prompt_template_version": template_snapshot["template_version"],
                "prompt_template_hash": template_snapshot["template_hash"],
                "prompt_variables_hash": final_prompt["variables_hash"],
                "final_prompt_hash": final_prompt["final_prompt_hash"],
                "raw_prompt_stored": final_prompt["raw_prompt_stored"],
                "reason_code": "raw_prompt_policy_default",
            },
            "input_usage_refs": [],
            "output_artifact_refs": [output_artifact_id] if mode == "patch" and response.get("patch_suggestions") else [],
            "created_at": _now_iso(),
            "extensions": {
                "diff_source_refs": context_envelope.get("diff_source_refs") or [],
                "selected_hunk_refs": context_envelope.get("selected_hunk_refs") or [],
            },
        }
        service.upsert_execution_provenance(goal_id=goal_id, provenance=provenance_payload)
        if mode == "patch" and response.get("patch_suggestions"):
            artifact_ref = f"ai-diff:patch:{response_hash[:16]}"
            try:
                service.record_output_artifact(
                    goal_id=goal_id,
                    output_artifact={
                        "schema": "goal_output_artifact.v1",
                        "output_artifact_id": output_artifact_id,
                        "goal_id": goal_id,
                        "task_id": f"diff3-{mode}",
                        "worker_id": "operator-tui-ai-diff",
                        "artifact_type": "patch_suggestion",
                        "created_at": _now_iso(),
                        "input_usage_refs": [],
                        "artifact_ref": artifact_ref,
                        "content_hash": response_hash,
                        "status": "created",
                        "provenance_summary": "AI diff patch suggestion (not applied)",
                        "provenance_id": provenance_id,
                        "execution_id": execution_id,
                        "provenance_kind": "worker_execution",
                    },
                )
            except GoalArtifactServiceError:
                pass

    return {
        "status": "success",
        "reason_code": "",
        "response": response,
        "context_envelope": context_envelope,
        "prompt_template_ref": template_ref,
        "final_prompt_hash": final_prompt["final_prompt_hash"],
        "provenance_id": provenance_id if goal_id else "",
        "output_artifact_id": output_artifact_id if goal_id and mode == "patch" and response.get("patch_suggestions") else "",
    }

