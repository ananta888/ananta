from __future__ import annotations

from typing import Any

from agent.services.mermaid_planning_extractor import extract_mermaid_task_candidates
from agent.services.planning_utils import parse_subtasks_from_llm_response, strip_markdown_fences, extract_json_payload


DEFAULT_CHAIN = ["strict_json", "strip_markdown_fence", "extract_first_json_block", "mermaid_graph_extract", "llm_repair"]


def run_parser_chain(raw_text: str, *, chain: list[str] | None = None, default_priority: str = "Medium") -> dict[str, Any]:
    text = str(raw_text or "")
    steps = list(chain or DEFAULT_CHAIN)
    trace: list[dict[str, Any]] = []

    for step in steps:
        if step == "strict_json":
            tasks = parse_subtasks_from_llm_response(text, default_priority=default_priority)
            trace.append({"step": step, "tasks": len(tasks)})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "strip_markdown_fence":
            candidate = strip_markdown_fences(text)
            tasks = parse_subtasks_from_llm_response(candidate, default_priority=default_priority)
            trace.append({"step": step, "tasks": len(tasks)})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "extract_first_json_block":
            payload = extract_json_payload(text)
            tasks = parse_subtasks_from_llm_response(payload or "", default_priority=default_priority)
            trace.append({"step": step, "tasks": len(tasks)})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "mermaid_graph_extract":
            res = extract_mermaid_task_candidates(text)
            tasks = list(res.get("subtasks") or []) if res.get("ok") else []
            trace.append({"step": step, "tasks": len(tasks), "ok": bool(res.get("ok"))})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "llm_repair":
            trace.append({"step": step, "tasks": 0, "note": "delegated_to_strategy_repair"})

    return {"subtasks": [], "trace": trace, "used_step": None}
