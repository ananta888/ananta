from __future__ import annotations

import hashlib
import json
from typing import Any

from worker.core.model_provider import WorkerModelProvider


def _safe_json_parse(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def propose_patch_with_model(
    *,
    model_provider: WorkerModelProvider | None,
    prompt: str,
    task_id: str,
    capability_id: str,
    base_ref: str = "HEAD",
    prompt_template_version: str = "worker_coding_prompt_v1",
) -> dict[str, Any]:
    if model_provider is None:
        return {
            "status": "degraded",
            "reason": "missing_model_provider",
            "llm_used": False,
            "fallback_reason": "no_model_configured",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
        }
    provider_result = model_provider.complete(prompt=prompt, prompt_template_version=prompt_template_version)
    parsed = _safe_json_parse(provider_result.text)
    if parsed is None:
        return {
            "status": "degraded",
            "reason": "invalid_model_output",
            "llm_used": True,
            "raw_output_preview": provider_result.text[:240],
            "model_metadata": provider_result.metadata,
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
        }
    patch_text = str(parsed.get("patch") or "")
    if patch_text.strip():
        patch_hash = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        changed_files = [str(item).strip() for item in list(parsed.get("changed_files") or []) if str(item).strip()]
        return {
            "status": "ok",
            "mode": "patch_artifact",
            "llm_used": True,
            "model_metadata": provider_result.metadata,
            "artifact": {
                "schema": "patch_artifact.v1",
                "task_id": str(task_id).strip(),
                "capability_id": str(capability_id).strip(),
                "base_ref": str(base_ref).strip() or "HEAD",
                "patch": patch_text,
                "patch_hash": patch_hash,
                "changed_files": changed_files,
                "risk_classification": str(parsed.get("risk_classification") or "high"),
                "expected_effects": [str(item).strip() for item in list(parsed.get("expected_effects") or []) if str(item).strip()],
            },
        }
    edit_plan = list(parsed.get("edit_plan") or [])
    if edit_plan:
        return {
            "status": "ok",
            "mode": "edit_plan",
            "llm_used": True,
            "model_metadata": provider_result.metadata,
            "artifact": {
                "schema": "worker_edit_plan.v1",
                "task_id": str(task_id).strip(),
                "capability_id": str(capability_id).strip(),
                "steps": [str(item).strip() for item in edit_plan if str(item).strip()],
            },
        }
    return {
        "status": "degraded",
        "reason": "missing_patch_or_edit_plan",
        "llm_used": True,
        "model_metadata": provider_result.metadata,
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
    }
