"""Repair-prompt building and plan-coverage checks for LLM-based planning.

Split out of ``planning_strategies`` (SRP): these pure helpers build the
follow-up prompts used when a planning response is unstructured, truncated or
lacks an execution path, and decide whether a new-project plan covers execution.
``LLMPlanningStrategy`` keeps them reachable under their historic static-method
names.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def compact_mode_data_for_prompt(mode_data: dict[str, Any] | None, *, max_chars: int = 4000) -> dict[str, Any]:
    data = dict(mode_data or {})
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= max_chars:
        return data
    compact: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"reference_profile_plan", "repository_map", "repo_context", "files"}:
            compact[key] = "<<omitted_for_prompt_size>>"
            continue
        if isinstance(value, str) and len(value) > 600:
            compact[key] = value[:600] + "...(truncated)"
        elif isinstance(value, list) and len(value) > 20:
            compact[key] = value[:20]
        elif isinstance(value, dict) and len(value) > 30:
            compact[key] = {k: value[k] for k in list(value.keys())[:30]}
        else:
            compact[key] = value
    compact_text = json.dumps(compact, ensure_ascii=False)
    if len(compact_text) > max_chars:
        return {"mode_data_summary": compact_text[:max_chars]}
    return compact


def compact_repair_output(previous_output: str, *, limit: int) -> str:
    text = str(previous_output or "").strip()
    if len(text) <= limit:
        return text
    head = max(200, limit // 2)
    tail = max(120, limit - head - 40)
    return f"{text[:head]}\n...[truncated]...\n{text[-tail:]}"


def looks_truncated_response(raw_response: str, parse_diag: dict[str, Any] | None = None) -> bool:
    diag = dict(parse_diag or {})
    warnings = {str(item or "").strip().lower() for item in list(diag.get("warnings") or [])}
    if {"truncated_json_recovered", "truncated_json_key_value_recovered"} & warnings:
        return True
    text = str(raw_response or "").strip()
    if not text:
        return False
    if len(text) > 24 and text[-1] in {",", ":", "\\", "'", '"', "[", "{"}:
        return True
    if text.count("{") > text.count("}") or text.count("[") > text.count("]"):
        return True
    format_error_codes = {str(item or "").strip().lower() for item in list(diag.get("format_error_codes") or [])}
    if {"unterminated_string", "unexpected_eof", "truncated"} & format_error_codes:
        return True
    return False


def format_adaptive_repair_guidance(*, output_shape: str | None, preferred_output_format: str) -> str:
    shape = str(output_shape or "").strip().lower()
    preferred = str(preferred_output_format or "json").strip().lower() or "json"
    if shape == "partial_json":
        return (
            "The previous answer was truncated. Return a shorter, complete plan with fewer words per task and no extra commentary."
        )
    if shape in {"json_in_markdown_fence", "json_extracted"}:
        return (
            "The previous answer already used markdown-wrapped JSON or embedded JSON. "
            "Preserve that style if it helps, but make the JSON block complete and parseable."
        )
    if shape in {"markdown_bullets", "numbered_steps"}:
        return (
            "The previous answer used markdown bullets or numbered steps. "
            "You may keep that style if it is easier, but make each task fully structured and concrete."
        )
    if shape in {"strict_json_array", "strict_json_object"}:
        return "Keep the JSON structure, but repair completeness and missing fields."
    if preferred == "markdown":
        return (
            "The model may prefer markdown. A concise markdown list is acceptable during repair, "
            "as long as each task stays concrete and can be normalized back into structured tasks."
        )
    if preferred == "yaml":
        return (
            "The model may prefer YAML. Keep keys short and avoid nested prose so the output can be normalized reliably."
        )
    return (
        f"Prefer {preferred.upper()} if that is your natural format, but keep the response structured and parseable."
    )


def build_planning_repair_prompt(
    *,
    goal: str,
    context: str | None,
    max_subtasks: int,
    previous_output: str,
    mode: str = "generic",
    mode_data: Optional[dict] = None,
    output_shape: str | None = None,
    preferred_output_format: str = "json",
) -> str:
    prompt = (
        "Der vorherige Planungs-Output war unstrukturiert oder leer.\n"
        "Erzeuge jetzt einen reparierten Plan in einer strukturierten, gut parsebaren Form.\n\n"
        f"ZIEL:\n{goal}\n\n"
    )
    if mode != "generic" and mode_data:
        prompt = f"{prompt}STEUERUNGSDATEN (Modus: {mode}):\n{json.dumps(mode_data, indent=2)}\n\n"

    prompt = (
        f"{prompt}"
        "ANFORDERUNGEN:\n"
        f"1. Liefere mindestens 5 und hoechstens {max_subtasks} Teilaufgaben.\n"
        "2. Jede Teilaufgabe muss title, description, priority enthalten.\n"
        "3. Fuelle die Pflichtphasen setup, implementation, execution, verification und summary ab, wenn es sich um ein neues Softwareprojekt handelt.\n"
        "4. Jede description muss einen konkreten Output nennen (Dateipfad, Endpoint, Command oder Artifact).\n"
        "5. priority nur: High, Medium, Low.\n"
        "6. depends_on als Liste von Schrittnummern als Strings (z.B. [\"1\"]).\n"
        "7. Keine Erklaerungen oder Meta-Kommentare.\n\n"
        f"FORMAT-HINWEIS: {format_adaptive_repair_guidance(output_shape=output_shape, preferred_output_format=preferred_output_format)}\n\n"
        "AUSGABEFORMAT (nur JSON-Array):\n"
        "[\n"
        '  {"title":"...","description":"...","priority":"High|Medium|Low","depends_on":[]}\n'
        "]\n\n"
        "VORHERIGER FEHLERHAFTER OUTPUT:\n"
        f"{compact_repair_output(previous_output, limit=1400)}"
    )
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


def build_new_project_execution_repair_prompt(
    *,
    goal: str,
    context: str | None,
    max_subtasks: int,
    previous_output: str,
    mode_data: Optional[dict] = None,
    output_shape: str | None = None,
    preferred_output_format: str = "json",
) -> str:
    prompt = (
        "Der vorherige Plan fuer new_software_project enthaelt keinen klaren Execution-Pfad oder ist abgeschnitten.\n"
        "Erzeuge jetzt einen reparierten Plan in einer strukturierten, gut parsebaren Form.\n\n"
        f"ZIEL:\n{goal}\n\n"
    )
    if mode_data:
        prompt = f"{prompt}STEUERUNGSDATEN:\n{json.dumps(compact_mode_data_for_prompt(mode_data, max_chars=1200), ensure_ascii=False, indent=2)}\n\n"
    prompt = (
        f"{prompt}"
        "MUSS-KRITERIEN:\n"
        f"1. Liefere mindestens 5 und hoechstens {max_subtasks} Teilaufgaben.\n"
        "2. Enthalte mindestens eine konkrete Datei-/Projektstruktur-Aufgabe (Dateien oder Ordner anlegen/aktualisieren).\n"
        "3. Enthalte mindestens eine konkrete Verifikations-Aufgabe (Test/Check/Run/Verify inkl. Ergebnisnachweis).\n"
        "4. Enthalte mindestens eine konkrete Ausfuehrungs-Aufgabe mit Run- oder Build-Command.\n"
        "5. Fuelle die Pflichtphasen setup, implementation, execution, verification und summary ab.\n"
        "6. Jede Teilaufgabe muss title, description, priority enthalten.\n"
        "7. priority nur: High, Medium, Low.\n"
        "8. depends_on als Liste von Schrittnummern als Strings (z.B. [\"1\"]).\n"
        "9. Keine Erklaerungen oder Meta-Kommentare.\n\n"
        f"FORMAT-HINWEIS: {format_adaptive_repair_guidance(output_shape=output_shape, preferred_output_format=preferred_output_format)}\n\n"
        "AUSGABEFORMAT (nur JSON-Array):\n"
        "[\n"
        '  {"title":"...","description":"...","priority":"High|Medium|Low","depends_on":[]}\n'
        "]\n\n"
        "VORHERIGER OUTPUT:\n"
        f"{compact_repair_output(previous_output, limit=1200)}"
    )
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


def build_new_project_truncation_repair_prompt(
    *,
    goal: str,
    context: str | None,
    max_subtasks: int,
    previous_output: str,
    mode_data: Optional[dict] = None,
    output_shape: str | None = None,
    preferred_output_format: str = "json",
) -> str:
    prompt = (
        "Der vorherige Plan fuer new_software_project wurde abgeschnitten.\n"
        "Gib jetzt eine komplette, kurze und strukturiert parsebare Version aus.\n\n"
        f"ZIEL:\n{goal}\n\n"
    )
    if mode_data:
        compact_mode_data = compact_mode_data_for_prompt(mode_data, max_chars=800)
        prompt = f"{prompt}STEUERUNGSDATEN:\n{json.dumps(compact_mode_data, ensure_ascii=False, indent=2)}\n\n"
    prompt = (
        f"{prompt}"
        "ANWEISUNG:\n"
        f"- Liefere mindestens 5 und hoechstens {max_subtasks} Teilaufgaben.\n"
        "- Repariere nur den fehlenden oder abgeschnittenen Teil und respektiere den Stil des bisherigen Outputs.\n"
        "- Keine Erklaerungen oder Rueckfragen.\n"
        "- Nutze konkrete Tasks fuer setup, implementation, execution, verification und summary.\n\n"
        f"FORMAT-HINWEIS: {format_adaptive_repair_guidance(output_shape=output_shape, preferred_output_format=preferred_output_format)}\n\n"
        "VORHERIGER TEIL-OUTPUT:\n"
        f"{compact_repair_output(previous_output, limit=800)}\n\n"
        "AUSGABEFORMAT: Nur ein JSON-Array."
    )
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


def has_new_project_execution_coverage(subtasks: list[dict[str, Any]]) -> bool:
    """Strict structural coverage check for new_software_project plans.

    Only structured execution signals count:
    - execution-relevant task kinds must carry required expected_artifacts
    - at least one verification_spec must be present
    """
    has_required_verification = False
    has_required_workspace_artifact = False
    execution_task_seen = False
    execution_kinds = {"coding", "testing", "ops"}
    for item in subtasks or []:
        task_kind = str(item.get("task_kind") or "").strip().lower()
        expected = [dict(x) for x in list(item.get("expected_artifacts") or []) if isinstance(x, dict)]
        verification_spec = dict(item.get("verification_spec") or {})
        if verification_spec:
            has_required_verification = True
        if task_kind not in execution_kinds:
            continue
        execution_task_seen = True
        required_artifacts = [a for a in expected if bool(a.get("required", False))]
        if not required_artifacts:
            return False
        if any(
            str(a.get("kind") or "").strip().lower()
            in {"workspace_change", "workspace_change_set", "generated_file", "project_structure_manifest"}
            for a in required_artifacts
        ):
            has_required_workspace_artifact = True
    if not execution_task_seen:
        return False
    return has_required_workspace_artifact and has_required_verification
