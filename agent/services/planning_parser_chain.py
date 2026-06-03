from __future__ import annotations

import ast
import json
from typing import Any

from agent.services.mermaid_planning_extractor import extract_mermaid_task_candidates
from agent.services.planning_parse_primitives import (
    extract_json_payload,
    extract_task_items_from_payload,
    normalize_subtask,
    strip_markdown_fences,
)


DEFAULT_CHAIN = ["strict_json", "strip_markdown_fence", "extract_first_json_block", "mermaid_graph_extract", "llm_repair"]


def _parse_subtasks_quick(text: str, *, default_priority: str) -> list[dict[str, Any]]:
    cleaned = strip_markdown_fences(text or "")
    payload = extract_json_payload(cleaned) or cleaned
    parsed: Any = None
    try:
        parsed = json.loads(payload)
    except Exception:
        try:
            parsed = ast.literal_eval(payload)
        except Exception:
            parsed = None
    if parsed is not None:
        items = extract_task_items_from_payload(parsed)
        normalized = [normalize_subtask(item, default_priority=default_priority) for item in items]
        return [item for item in normalized if item]
    # YAML-like fallback:
    # - title: Setup
    #   description: Create venv
    #   priority: High
    yaml_tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if current:
                n = normalize_subtask(current, default_priority=default_priority)
                if n:
                    yaml_tasks.append(n)
            current = {}
            line = line[2:].strip()
        if ":" in line and current is not None:
            key, value = line.split(":", 1)
            k = key.strip().lower()
            v = value.strip().strip('"').strip("'")
            if k in {"title", "description", "priority"}:
                current[k] = v
            elif k == "depends_on":
                current[k] = []
    if current:
        n = normalize_subtask(current, default_priority=default_priority)
        if n:
            yaml_tasks.append(n)
    if yaml_tasks:
        return yaml_tasks

    tasks: list[dict[str, Any]] = []
    for line in cleaned.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            n = normalize_subtask({"description": s.lstrip("-*1234567890. ").strip()}, default_priority=default_priority)
            if n:
                tasks.append(n)
    return tasks


def run_parser_chain(raw_text: str, *, chain: list[str] | None = None, default_priority: str = "Medium") -> dict[str, Any]:
    text = str(raw_text or "")
    steps = list(chain or DEFAULT_CHAIN)
    trace: list[dict[str, Any]] = []

    for step in steps:
        if step == "strict_json":
            tasks = _parse_subtasks_quick(text, default_priority=default_priority)
            trace.append({"step": step, "tasks": len(tasks)})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "strip_markdown_fence":
            candidate = strip_markdown_fences(text)
            tasks = _parse_subtasks_quick(candidate, default_priority=default_priority)
            trace.append({"step": step, "tasks": len(tasks)})
            if tasks:
                return {"subtasks": tasks, "trace": trace, "used_step": step}
        elif step == "extract_first_json_block":
            payload = extract_json_payload(text)
            tasks = _parse_subtasks_quick(payload or "", default_priority=default_priority)
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
