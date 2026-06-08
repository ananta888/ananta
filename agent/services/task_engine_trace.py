"""Task Engine pipeline trace helpers (te-008).

Stamps intent_router and task_class_resolver stage results into the
``pipeline`` dict that lives on ``TaskScopedStepProposeResponse``.

Usage::

    from agent.services.task_engine_trace import stamp_te_pipeline

    proposal_response.pipeline = stamp_te_pipeline(
        existing_pipeline=proposal_response.pipeline,
        intent_result=ir,
        class_result=cr,
        handler_id="list_files",
    )
"""
from __future__ import annotations

import time
from typing import Any


def stamp_te_pipeline(
    existing_pipeline: dict[str, Any] | None,
    *,
    intent_result: Any | None = None,
    class_result: Any | None = None,
    handler_id: str | None = None,
    bypassed_llm: bool = False,
) -> dict[str, Any]:
    """Return an updated pipeline dict with task_engine stage entries appended."""
    pipeline = dict(existing_pipeline or {})
    stages: list[dict[str, Any]] = list(pipeline.get("stages") or [])
    ts = time.time()

    if intent_result is not None:
        stages.append({
            "stage": "task_intent_router",
            "ts": ts,
            "intent": getattr(intent_result, "intent", None),
            "task_class": getattr(intent_result, "task_class", None),
            "llm_required": getattr(intent_result, "llm_required", None),
            "source": getattr(intent_result, "source", None),
            "deterministic_handler_id": getattr(intent_result, "deterministic_handler_id", None),
        })

    if class_result is not None:
        stages.append({
            "stage": "task_class_resolver",
            "ts": ts,
            "task_class": getattr(class_result, "task_class", None),
            "llm_required": getattr(class_result, "llm_required", None),
            "intent": getattr(class_result, "intent", None),
            "reason": getattr(class_result, "reason", None),
            "deterministic_handler_id": getattr(class_result, "deterministic_handler_id", None),
        })

    if handler_id is not None:
        stages.append({
            "stage": "deterministic_handler_dispatch",
            "ts": ts,
            "handler_id": handler_id,
            "bypassed_llm": bypassed_llm,
        })

    pipeline["stages"] = stages
    pipeline["task_engine_active"] = True
    pipeline["last_updated"] = ts
    return pipeline


def extract_te_summary(pipeline: dict[str, Any] | None) -> dict[str, Any]:
    """Extract a compact task_engine summary from a pipeline dict (for API/TUI read-models)."""
    stages = (pipeline or {}).get("stages") or []
    ir_stage = next((s for s in stages if s.get("stage") == "task_intent_router"), {})
    cr_stage = next((s for s in stages if s.get("stage") == "task_class_resolver"), {})
    dispatch = next((s for s in stages if s.get("stage") == "deterministic_handler_dispatch"), {})
    return {
        "intent": cr_stage.get("intent") or ir_stage.get("intent"),
        "task_class": cr_stage.get("task_class") or ir_stage.get("task_class"),
        "llm_required": cr_stage.get("llm_required"),
        "reason": cr_stage.get("reason"),
        "handler_id": dispatch.get("handler_id"),
        "bypassed_llm": dispatch.get("bypassed_llm", False),
    }
