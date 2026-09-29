"""Pure plan-repair helpers used by the planning pipeline.

Split out of ``planning_service`` (SRP): the minimal non-LLM fallback
subtask, the selective repair prompt for missing plan categories and the
structural repair of an invalid plan payload. ``PlanningService`` keeps the
static-method names and delegates here.
"""

from __future__ import annotations

from typing import Any


def build_minimal_non_llm_fallback_subtask(*, goal: str, mode: str = "generic") -> dict[str, Any]:
    goal_preview = str(goal or "").strip()[:160] or "Goal"
    title = "Create initial executable project skeleton"
    if mode == "new_software_project":
        title = "Create project skeleton and first runnable check"
    return {
        "title": title,
        "description": (
            f"Initialize a minimal executable baseline for goal: {goal_preview}. "
            "Create at least one concrete project artifact and one basic verification/check command."
        ),
        "priority": "High",
        "task_kind": "coding",
        "depends_on": [],
        "fallback_origin": "non_llm_minimal_task",
    }


def build_selective_repair_prompt(
    *,
    goal: str,
    mode: str,
    missing_categories: list[str],
    generic_task_indices: list[int],
    preferred_output_format: str,
    required_task_kinds: list[str] | None = None,
    error_codes: list[str] | None = None,
) -> str:
    effective_missing = list(missing_categories or [])
    if mode == "new_software_project" and not effective_missing:
        effective_missing = ["analysis", "infrastructure", "implementation", "tests", "review"]
    missing = ", ".join(effective_missing) if effective_missing else "none"
    generic = ", ".join(str(i) for i in generic_task_indices[:12]) if generic_task_indices else "none"
    missing_lines = "\n".join(
        f"- category={str(cat).strip().lower()}: mindestens 1 konkrete Aufgabe"
        for cat in effective_missing
        if str(cat).strip()
    )
    if not missing_lines:
        missing_lines = "- none"
    required_lines = "\n".join(
        f"- task_kind={str(kind).strip().lower()}"
        for kind in list(required_task_kinds or [])
        if str(kind).strip()
    ) or "- none"
    repair_error_codes = ", ".join(str(code).strip() for code in list(error_codes or []) if str(code).strip()) or "none"
    return (
        "Repair only missing or weak parts of the plan. Do not rewrite everything.\n"
        f"GOAL: {goal}\n"
        f"MODE: {mode}\n"
        f"MISSING_CATEGORIES: {missing}\n"
        f"GENERIC_TASK_INDEXES: {generic}\n"
        f"CONTRACT_ERROR_CODES: {repair_error_codes}\n"
        "Return only additional or replacement tasks needed to close gaps.\n"
        "WICHTIG:\n"
        "1) Liefere NUR JSON-Array.\n"
        "2) Jede Aufgabe MUSS diese Felder haben: title, description, task_kind, priority.\n"
        "3) task_kind-Mapping (GENAU diese Werte verwenden):\n"
        "   analysis → task_kind='analysis'\n"
        "   infrastructure → task_kind='ops'  (Docker, CI/CD, Env-Setup, Dockerfile, Pipeline)\n"
        "   implementation → task_kind='coding'\n"
        "   tests → task_kind='testing'\n"
        "   review → task_kind='review'\n"
        "4) Jede description MUSS einen konkreten Output nennen (Dateipfad, Endpoint, Command oder Artifact).\n"
        "5) Keine generischen Sammel-Tasks.\n"
        "6) Decke zwingend diese fehlenden Kategorien ab:\n"
        f"{missing_lines}\n"
        "7) Decke diese fehlenden task_kind-Werte direkt ab:\n"
        f"{required_lines}\n"
        f"Preferred output format: {preferred_output_format}.\n"
        "Keep fields short and concrete."
    )


def repair_invalid_plan_payload(payload: dict[str, Any], validation_errors: list[str]) -> dict[str, Any]:
    repaired = dict(payload or {})
    nodes = [dict(item) for item in list(repaired.get("nodes") or []) if isinstance(item, dict)]
    need_artifacts = any(str(err).startswith("missing_expected_artifacts:") for err in list(validation_errors or []))
    if need_artifacts:
        for node in nodes:
            task_kind = str(node.get("task_kind") or "").strip().lower()
            expected = [dict(a) for a in list(node.get("expected_artifacts") or []) if isinstance(a, dict)]
            if task_kind in {"coding", "testing", "ops"} and not expected:
                expected = [{"kind": "workspace_change", "required": True, "description": f"{node.get('node_key')}-output"}]
                node["expected_artifacts"] = expected
            verification_spec = dict(node.get("verification_spec") or {})
            if expected and not verification_spec.get("expected_artifacts"):
                verification_spec["expected_artifacts"] = expected
            node["verification_spec"] = verification_spec
    repaired["nodes"] = nodes
    return repaired
