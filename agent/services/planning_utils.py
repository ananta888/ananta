from __future__ import annotations

import json
import logging
import re
import ast
from typing import Any, Optional
from warnings import warn

from agent.services.execution_focused_planning import (
    EXECUTION_FOCUSED_GOAL_HINTS as _EXECUTION_FOCUSED_GOAL_HINTS,
)
from agent.services.execution_focused_planning import (
    build_execution_focused_goal_template as _build_execution_focused_goal_template,
)
from agent.services.execution_focused_planning import (
    match_execution_focused_goal_template,
)
from agent.services.planning_template_catalog import get_planning_template_catalog
from agent.services.planning_output_shape_classifier import classify_output_shape
from agent.services.planning_format_error_analyzer import analyze_format_errors
VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
SUSPICIOUS_TASK_PATTERNS = [
    r"\bignore\b",
    r"\bsystem:\b",
    r"\bassistant:\b",
    r"<\|im_start\|>",
    r"<script\b",
]

_ACTIONABLE_VERBS = (
    "implement",
    "create",
    "write",
    "run",
    "test",
    "verify",
    "configure",
    "update",
    "add",
    "build",
)
_CONCRETE_TARGET_HINTS = ("file", "endpoint", "command", "artifact", "/", ".py", ".md", ".json", "api")

def _load_goal_templates_from_catalog() -> dict[str, dict]:
    catalog = get_planning_template_catalog()
    loaded = catalog.load()
    templates: dict[str, dict] = {}
    for template in list(loaded.get("templates") or []):
        template_id = str(template.get("id") or "").strip()
        if not template_id:
            continue
        templates[template_id] = {
            "keywords": [str(item).strip() for item in list(template.get("keywords") or []) if str(item).strip()],
            "subtasks": [dict(item) for item in list(template.get("subtasks") or [])],
        }
    return templates


# Deprecated compatibility shim for legacy imports. Source of truth is the planning template catalog.
try:
    GOAL_TEMPLATES = _load_goal_templates_from_catalog()
except (OSError, ValueError):
    GOAL_TEMPLATES = {}

# Deprecated compatibility exports. Source of truth moved to execution_focused_planning.py.
EXECUTION_FOCUSED_GOAL_HINTS = _EXECUTION_FOCUSED_GOAL_HINTS
build_execution_focused_goal_template = _build_execution_focused_goal_template

PROMPT_INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all",
    "disregard",
    "forget everything",
    "new instructions",
    "system:",
    "assistant:",
    "<|im_start|",
    "<|im_end|>",
    "### instruction",
    "### system",
    "act as",
    "pretend you are",
    "you are now",
    "simulate",
    "jailbreak",
    "DAN mode",
    "do anything now",
]


def strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].startswith("```"):
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    return cleaned.strip()


def extract_json_payload(text: str) -> str | None:
    cleaned = strip_markdown_fences(text)
    if not cleaned:
        return None
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")
    if first_brace == -1 and first_bracket == -1:
        return None
    if first_brace == -1:
        start = first_bracket
        end = cleaned.rfind("]")
    elif first_bracket == -1:
        start = first_brace
        end = cleaned.rfind("}")
    else:
        start = min(first_brace, first_bracket)
        end = cleaned.rfind("}" if start == first_brace else "]")
    if start < 0 or end < start:
        return None
    return cleaned[start : end + 1].strip()


def contains_suspicious_text(value: str) -> bool:
    lower = str(value or "").strip().lower()
    if not lower:
        return False
    return any(re.search(pattern, lower) for pattern in SUSPICIOUS_TASK_PATTERNS)


def normalize_priority(value: str | None, default_priority: str = "Medium") -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_PRIORITIES:
        return VALID_PRIORITIES[raw]
    return VALID_PRIORITIES.get(str(default_priority or "").strip().lower(), "Medium")


def normalize_subtask(item: dict, default_priority: str = "Medium") -> dict | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("name") or "").strip()
    description = str(item.get("description") or item.get("task") or title).strip()
    if not title:
        title = description[:80].strip()
    if not title or not description:
        return None
    if contains_suspicious_text(title) or contains_suspicious_text(description):
        return None
    depends_on = item.get("depends_on")
    if not isinstance(depends_on, list):
        depends_on = []
    normalized_depends_on = [str(dep).strip() for dep in depends_on if str(dep).strip()][:5]
    dependency_mode = str(item.get("dependency_mode") or "").strip().lower()
    if dependency_mode not in {"parallel", "explicit", "sequential"}:
        dependency_mode = "explicit" if normalized_depends_on else "sequential"
    if "__parallel__" in normalized_depends_on:
        dependency_mode = "parallel"
        normalized_depends_on = []
    return {
        "title": title[:200],
        "description": description[:2000],
        "priority": normalize_priority(item.get("priority"), default_priority),
        "depends_on": normalized_depends_on,
        "dependency_mode": dependency_mode,
    }


def _postprocess_subtasks(subtasks: list[dict], *, max_items: int = 16) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for task in list(subtasks or []):
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or "").strip().lower()
        desc = str(task.get("description") or "").strip().lower()
        if not title or not desc:
            continue
        key = (title, desc)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(task)
        if len(deduped) >= max_items:
            break
    return deduped


def extract_task_items_from_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("tasks", "subtasks", "sub_tasks", "items", "steps", "children"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    # Common LLM response variants for plan-like outputs.
    for extractor in (_extract_actionable_steps, _extract_roadmap_tasks, _extract_nested_dependencies):
        extracted = extractor(payload)
        if extracted:
            return extracted

    if any(str(payload.get(key) or "").strip() for key in ("title", "name", "description", "task", "detail", "recommendation")):
        return [payload]

    return _collect_task_like_items(payload)


def _dependency_list(entry: dict) -> list:
    return entry.get("depends_on") if isinstance(entry.get("depends_on"), list) else []


def _extract_actionable_steps(payload: dict) -> list[object]:
    actionable_steps = payload.get("actionable_steps")
    if not isinstance(actionable_steps, list):
        return []
    extracted_steps: list[object] = []
    for step in actionable_steps:
        if isinstance(step, dict):
            extracted_steps.append(
                {
                    "title": step.get("title") or step.get("name") or step.get("step") or "",
                    "description": step.get("detail") or step.get("description") or step.get("title") or "",
                    "priority": step.get("priority") or payload.get("priority"),
                    "depends_on": _dependency_list(step),
                }
            )
        elif isinstance(step, str):
            extracted_steps.append({"description": step, "priority": payload.get("priority")})
    return extracted_steps


def _extract_roadmap_tasks(payload: dict) -> list[object]:
    roadmap = payload.get("implementation_roadmap")
    if not isinstance(roadmap, dict):
        return []
    roadmap_tasks: list[object] = []
    for phase_value in roadmap.values():
        if not isinstance(phase_value, dict):
            continue
        phase_goal = str(phase_value.get("goal") or "").strip()
        phase_tasks = phase_value.get("tasks")
        if not isinstance(phase_tasks, list):
            continue
        for task in phase_tasks:
            entry = _roadmap_task_entry(task, phase_goal=phase_goal, payload=payload)
            if entry is not None:
                roadmap_tasks.append(entry)
    return roadmap_tasks


def _roadmap_task_entry(task: object, *, phase_goal: str, payload: dict) -> dict | None:
    if isinstance(task, str):
        title = task[:80]
        if phase_goal:
            title = f"{phase_goal}: {title}"[:80]
        return {
            "title": title,
            "description": task,
            "priority": payload.get("priority"),
            "depends_on": [],
        }
    if isinstance(task, dict):
        return {
            "title": task.get("title") or task.get("name") or "",
            "description": task.get("detail") or task.get("description") or task.get("title") or "",
            "priority": task.get("priority") or payload.get("priority"),
            "depends_on": _dependency_list(task),
        }
    return None


def _extract_nested_dependencies(payload: dict) -> list[object]:
    nested_dependencies = payload.get("depends_on")
    if not isinstance(nested_dependencies, list):
        return []
    extracted: list[object] = []
    for entry in nested_dependencies:
        if isinstance(entry, dict):
            extracted.append(
                {
                    "title": entry.get("title") or entry.get("name") or "",
                    "description": entry.get("description") or entry.get("task") or entry.get("name") or "",
                    "priority": entry.get("priority") or payload.get("priority"),
                    "depends_on": _dependency_list(entry),
                }
            )
        elif isinstance(entry, str):
            extracted.append({"description": entry, "priority": payload.get("priority")})
    return extracted


def _looks_task_like(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    has_title = any(str(obj.get(k) or "").strip() for k in ("title", "name", "task", "step", "layer", "area"))
    has_desc = any(str(obj.get(k) or "").strip() for k in ("description", "detail", "content", "responsibility", "recommendation"))
    return bool(has_title or has_desc)


def _collect_task_like_items(payload: dict, *, max_nodes: int = 20000) -> list[object]:
    """Generic recursive fallback: collect list entries that look like actionable task items."""
    collected: list[object] = []
    stack: list[object] = [payload]
    visited: set[int] = set()
    visited_count = 0

    while stack and visited_count < max_nodes:
        node = stack.pop()
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        visited_count += 1

        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, list):
                    _collect_from_task_list(collected, key, value, payload)
                    stack.extend(value)
                elif isinstance(value, dict):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return collected


def _collect_from_task_list(collected: list[object], key: object, value: list, payload: dict) -> None:
    key_lower = str(key).lower()
    if not ("task" in key_lower or "step" in key_lower or "action" in key_lower):
        return
    for item in value:
        if _looks_task_like(item):
            collected.append(item)
        elif isinstance(item, str):
            collected.append({"description": item, "priority": payload.get("priority")})


def _parse_diagnostics(
    *,
    parse_mode: str,
    confidence: str,
    warnings: list[str],
    shape: dict[str, Any],
    chain_result: dict[str, Any],
    format_error_codes: list,
) -> dict[str, Any]:
    return {
        "parse_mode": parse_mode,
        "confidence": confidence,
        "warnings": warnings,
        "output_shape": shape.get("primary_shape"),
        "detected_shapes": list(shape.get("detected_shapes") or []),
        "format_error_codes": format_error_codes,
        "parser_trace": list(chain_result.get("trace") or []),
    }


def _normalized_subtasks(items: list[object], default_priority: str) -> list[dict]:
    normalized = [normalize_subtask(item, default_priority=default_priority) for item in items]
    return _postprocess_subtasks([item for item in normalized if item])


def _parse_structured_payload(json_payload: str, cleaned: str) -> tuple[object | None, str, str]:
    """Parse strict JSON, then Python-literal payloads; return (parsed, parse_mode, confidence)."""
    try:
        parsed = json.loads(json_payload)
        parse_mode = "strict_json" if json_payload.strip() == cleaned.strip() else "json_extracted"
        return parsed, parse_mode, "high"
    except json.JSONDecodeError:
        # Fallback for Python-literal style payloads (single quotes, True/False/None).
        try:
            return ast.literal_eval(json_payload), "python_literal", "medium"
        except Exception:
            return None, "parse_failed", "low"


def _parse_object_candidate(candidate: str) -> dict | None:
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except Exception:
        try:
            obj = ast.literal_eval(candidate)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


def _balanced_object_spans(text: str) -> list[tuple[int, int]]:
    """Top-level ``{...}`` spans outside JSON strings, tolerant of truncated tails."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "\"":
                in_string = False
            continue
        if ch == "\"":
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, idx + 1))
                start = -1
    return spans


def _extract_partial_json_objects(text: str) -> list[dict[str, Any]]:
    """Truncation-safe fallback: extract JSON objects from partial arrays/text."""
    objects: list[dict[str, Any]] = []
    for start, end in _balanced_object_spans(text):
        obj = _parse_object_candidate(text[start:end])
        if obj is not None:
            objects.append(obj)
    return objects


def _salvage_title_description_pairs(json_payload: str, default_priority: str) -> list[dict[str, Any]]:
    """Last-resort salvage for truncated pseudo-JSON: title/description pairs from raw text."""
    title_matches = re.findall(r'"title"\s*:\s*"([^"\n]{1,180})"', json_payload)
    desc_matches = re.findall(r'"description"\s*:\s*"([^"\n]{1,600})"', json_payload)
    recovered: list[dict[str, Any]] = []
    for idx, title in enumerate(title_matches):
        desc = desc_matches[idx] if idx < len(desc_matches) else f"Execute task: {title}"
        recovered.append(
            {
                "title": str(title).strip(),
                "description": str(desc).strip(),
                "priority": default_priority,
                "depends_on": [],
            }
        )
    return recovered


def _recover_truncated_subtasks(json_payload: str, default_priority: str) -> tuple[list[dict], str, str] | None:
    """Return ``(subtasks, parse_mode, warning)`` recovered from an unparseable payload."""
    objects = _extract_partial_json_objects(json_payload)
    if objects:
        subtasks = _normalized_subtasks(extract_task_items_from_payload(objects), default_priority)
        if subtasks:
            return subtasks, "partial_json_objects", "truncated_json_recovered"
    recovered = _salvage_title_description_pairs(json_payload, default_priority)
    if recovered:
        subtasks = _normalized_subtasks(recovered, default_priority)
        if subtasks:
            return subtasks, "kv_text_salvage", "truncated_json_key_value_recovered"
    return None


def _bullet_subtasks(cleaned: str, default_priority: str) -> list[dict]:
    tasks = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            desc = line.lstrip("-*1234567890. ").strip()
            normalized = normalize_subtask(
                {"description": desc, "priority": default_priority},
                default_priority=default_priority,
            )
            if normalized:
                tasks.append(normalized)
    return tasks


def parse_subtasks_with_diagnostics(response: str, default_priority: str = "Medium") -> tuple[list[dict], dict[str, Any]]:
    from agent.services.planning_parser_chain import run_parser_chain

    shape = classify_output_shape(response)
    chain_result = run_parser_chain(response, default_priority=default_priority)

    def diagnostics(parse_mode: str, confidence: str, warnings: list[str], *, format_errors: bool = True) -> dict:
        return _parse_diagnostics(
            parse_mode=parse_mode,
            confidence=confidence,
            warnings=warnings,
            shape=shape,
            chain_result=chain_result,
            format_error_codes=analyze_format_errors(response, parse_result={}) if format_errors else [],
        )

    if chain_result.get("subtasks"):
        subtasks = _postprocess_subtasks(list(chain_result.get("subtasks") or []))
        parse_mode = str(chain_result.get("used_step") or "parser_chain")
        return subtasks, diagnostics(parse_mode, "medium", [], format_errors=False)

    cleaned = strip_markdown_fences(response)
    json_payload = extract_json_payload(cleaned) or cleaned
    warnings: list[str] = []
    parsed, parse_mode, confidence = _parse_structured_payload(json_payload, cleaned)

    if parsed is None:
        recovered = _recover_truncated_subtasks(json_payload, default_priority)
        if recovered is not None:
            subtasks, recovered_mode, warning = recovered
            return subtasks, diagnostics(recovered_mode, "low", [warning])

    if parsed is not None:
        if isinstance(parsed, dict):
            if isinstance(parsed.get("implementation_roadmap"), dict):
                parse_mode = "roadmap_extracted"
                confidence = "medium"
            elif isinstance(parsed.get("depends_on"), list):
                parse_mode = "nested_extracted"
                confidence = "medium"
        subtasks = _normalized_subtasks(extract_task_items_from_payload(parsed), default_priority)
        if not subtasks:
            parse_mode = "parse_failed"
            confidence = "low"
            warnings.append("no_subtasks_extracted")
        return subtasks, diagnostics(parse_mode, confidence, warnings)

    tasks = _bullet_subtasks(cleaned, default_priority)
    if tasks:
        return _postprocess_subtasks(tasks), diagnostics("bullet_fallback", "low", warnings)
    warnings.append("unparseable_response")
    return [], diagnostics("parse_failed", "low", warnings)


def parse_subtasks_from_llm_response(response: str, default_priority: str = "Medium") -> list[dict]:
    subtasks, _diag = parse_subtasks_with_diagnostics(response, default_priority=default_priority)
    return subtasks


def parse_followup_analysis(raw_response: str, default_priority: str = "Medium") -> dict:
    """Parse optional follow-up analysis from model response.

    ADVISORY ONLY — callers must NOT drive task completed-state from task_complete alone.
    Always check advisory=True and prefer artifact/verification evidence for completion decisions.
    Reason code advisory_parse_failed_ignored should be logged when this returns parse_error=True
    but artifact completion policy succeeds.
    """
    json_payload = extract_json_payload(raw_response)
    if not json_payload:
        return {
            "task_complete": None,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "missing_json",
            "advisory": True,
            "reason_code": "advisory_parse_failed_ignored",
        }
    try:
        parsed = json.loads(json_payload)
    except json.JSONDecodeError:
        return {
            "task_complete": None,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "invalid_json",
            "advisory": True,
            "reason_code": "advisory_parse_failed_ignored",
        }
    if not isinstance(parsed, dict):
        return {
            "task_complete": None,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "wrong_shape",
            "advisory": True,
            "reason_code": "advisory_parse_failed_ignored",
        }
    followups = parsed.get("followup_tasks")
    normalized_followups = []
    if isinstance(followups, list):
        normalized_followups = [
            item
            for item in (
                normalize_subtask(entry, default_priority=default_priority) for entry in followups
            )
            if item
        ][:5]
    suggestions = parsed.get("suggestions") if isinstance(parsed.get("suggestions"), list) else []
    cleaned_suggestions = [str(item).strip()[:240] for item in suggestions if str(item).strip()][:10]
    raw_complete = parsed.get("task_complete")
    # task_complete from model is advisory — None means "not stated", True/False are suggestions only.
    advisory_complete = bool(raw_complete) if raw_complete is not None else None
    return {
        "task_complete": advisory_complete,
        "needs_review": bool(parsed.get("needs_review", False)),
        "followup_tasks": normalized_followups,
        "suggestions": cleaned_suggestions,
        "parse_error": False,
        "advisory": True,
        "reason_code": None,
    }


def match_goal_template(goal: str) -> Optional[list[dict]]:
    warn(
        (
            "planning_utils.match_goal_template is deprecated. "
            "Use PlanningTemplateCatalog/TemplatePlanningStrategy instead."
        ),
        DeprecationWarning,
        stacklevel=2,
    )
    catalog = get_planning_template_catalog()
    exact_template = catalog.get_template(str(goal or "").strip())
    if exact_template is not None:
        return list(exact_template.get("subtasks") or [])

    lower_goal = str(goal or "").lower()
    tdd_keywords = ("tdd", "test-driven", "test driven", "test-first", "red green", "red-green")
    if any(keyword in lower_goal for keyword in tdd_keywords):
        tdd_template = catalog.get_template("tdd")
        if tdd_template is not None:
            return list(tdd_template.get("subtasks") or [])

    execution_focused_subtasks = match_execution_focused_goal_template(goal)
    if execution_focused_subtasks:
        return execution_focused_subtasks

    subtasks = catalog.resolve_subtasks(goal, exact_id_first=False)
    if subtasks:
        return subtasks
    return None


def try_load_repo_context(goal: str) -> Optional[str]:
    try:
        from agent.config import settings
        from agent.hybrid_orchestrator import HybridOrchestrator

        repo_root = settings.rag_repo_root or "."
        orchestrator = HybridOrchestrator(repo_root=repo_root)
        context_result = orchestrator.get_relevant_context(goal)
        if context_result and isinstance(context_result, dict) and context_result.get("context_text"):
            return str(context_result["context_text"])[:2000]
    except Exception as exc:
        logging.debug(f"Could not load repo context: {exc}")
    return None


def build_planning_prompt(goal: str, context: Optional[str] = None, max_tasks: int = 8) -> str:
    try:
        from agent.services.planning_prompt_registry import get_planning_prompt_registry

        resolved = get_planning_prompt_registry().resolve(
            goal=goal,
            context=context,
            mode="generic",
            language="de",
            model_family=None,
        )
        if str(resolved.prompt or "").strip():
            prompt = str(resolved.prompt)
            if context:
                prompt = prompt.replace("\nCONTEXT:\n", "\nKONTEXT:\n")
            return prompt
    except Exception:
        pass
    prompt = (
        "Du bist ein Projektplanungs-Assistent. Analysiere das folgende Ziel und "
        "zerlege es in konkrete, ausfuehrbare Teilaufgaben.\n\n"
        f"ZIEL:\n{goal}\n\n"
        "ANFORDERUNGEN:\n"
        f"1. Erstelle zwischen 5 und {max_tasks} Teilaufgaben, nie weniger als 5.\n"
        "2. Jede Teilaufgabe soll konkret und ausfuehrbar sein und genau einen klaren Output haben.\n"
        "3. Nutze diese Pflichtphasen, wenn es sich um ein neues Softwareprojekt handelt: setup, implementation, execution, verification, summary.\n"
        "4. Fuer Softwareprojekte muss mindestens je eine Aufgabe diese Kategorien abdecken: analysis, infrastructure, implementation, tests, review.\n"
        "5. Priorisiere nach Abhaengigkeiten (was muss zuerst erledigt werden).\n"
        "6. Verwende diese Prioritaeten: High, Medium, Low.\n"
        "7. Befehle duerfen KEIN 'sudo', 'su' oder Privilege-Escalation verwenden — "
        "Ausfuehrung erfolgt als normaler Nutzer in einem Docker-Container ohne Root-Rechte.\n"
        "8. Befehle duerfen KEIN 'systemctl', 'service' oder 'ss' verwenden — "
        "kein systemd/init.d in Docker, 'ss' nicht installiert. "
        "Alternativen: 'pgrep -x <name>' fuer Prozessstatus, 'netstat -tlnp' fuer Ports.\n"
        "9. Setze 'depends_on' nur wenn eine echte Reihenfolge-Abhaengigkeit besteht. "
        "Unabhaengige Diagnose- oder Verifikationsaufgaben sollen 'depends_on': [] haben.\n"
        "10. Vermeide Sammelaufgaben wie 'Vorbereitung', 'Setup', 'Finalisierung' ohne konkreten Output.\n\n"
        "AUSGABEFORMAT (nur JSON, keine Erklaerung):\n"
        "[\n"
        '  {"title": "Kurzer Titel", "description": "Detaillierte Beschreibung der Aufgabe", '
        '"priority": "High|Medium|Low", "depends_on": []},\n'
        "  ...\n"
        "]\n"
    )
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


def build_planning_prompt_en(goal: str, context: Optional[str] = None, max_tasks: int = 8) -> str:
    """English planning prompt — more compatible with small/embedded models like gemma-4-e4b."""
    try:
        from agent.services.planning_prompt_registry import get_planning_prompt_registry

        resolved = get_planning_prompt_registry().resolve(
            goal=goal,
            context=context,
            mode="generic",
            language="en",
            model_family=None,
        )
        if str(resolved.prompt or "").strip():
            return str(resolved.prompt)
    except Exception:
        pass
    try:
        from agent.services.system_prompt_catalog import get_system_prompt
        tpl = get_system_prompt("system.planning_fallback", "")
    except Exception:
        tpl = ""
    context_block = f"\nCONTEXT:\n{str(context).strip()}\n" if context else ""
    if tpl:
        return tpl.format(goal=goal, context=str(context or "").strip(),
                          max_tasks=max_tasks, context_block=context_block)
    prompt = (
        "You are a project planning assistant. Break down the following goal into concrete, "
        f"actionable subtasks. Output ONLY a valid JSON array with at least 5 and at most {max_tasks} tasks, "
        "no explanation, no markdown fences.\n\n"
        f"GOAL: {goal}\n\n"
        "RULES:\n"
        "- Use setup, implementation, execution, verification, summary phases for software projects\n"
        "- No generic umbrella tasks without a concrete deliverable\n"
        "- No sudo, su, or privilege escalation\n"
        "- Set depends_on only for real sequential dependencies\n\n"
        "OUTPUT FORMAT (JSON array only):\n"
        '[{"title":"Short title","description":"Detailed description",'
        '"priority":"High|Medium|Low","depends_on":[]}]\n'
    )
    if context:
        prompt = f"{prompt}\nCONTEXT:\n{str(context).strip()}\n"
    return prompt


def sanitize_input(text: str, max_length: int = 4000) -> str:
    if not text:
        return ""
    sanitized = text.strip()[:max_length]
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(re.escape(pattern), sanitized, flags=re.IGNORECASE):
            logging.warning(f"Potential prompt injection detected: {pattern}")
            sanitized = re.sub(re.escape(pattern), "", sanitized, flags=re.IGNORECASE)
    sanitized = " ".join(sanitized.split())
    return sanitized


def validate_goal(goal: str) -> tuple[bool, str]:
    if not goal or not goal.strip():
        return False, "goal_required"
    if len(goal) > 4000:
        return False, "goal_too_long"
    lower = goal.lower()
    critical_patterns = ["ignore previous instructions", "jailbreak", "DAN mode"]
    for pattern in critical_patterns:
        if pattern.lower() in lower:
            return False, "prompt_injection_detected"
    return True, ""
