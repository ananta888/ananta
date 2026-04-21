from __future__ import annotations

from typing import Any


BENCHMARK_SUITE_VERSION = "v1"

BENCHMARK_TASKS: list[dict[str, Any]] = [
    {
        "id": "repo-understanding",
        "title": "Repository verstehen",
        "use_case": "Repository verstehen",
        "goal": "Analysiere dieses Repository und nenne die naechsten sinnvollen Schritte.",
        "expected_strength": "goal_to_plan_traceability",
        "required_evidence": ["goal", "plan", "task_list", "next_steps"],
    },
    {
        "id": "bugfix-plan",
        "title": "Bugfix planbar und testbar machen",
        "use_case": "Bugfix planbar und testbar machen",
        "goal": "Plane einen kleinen Fix inklusive Risiko, Tests und Review-Hinweisen.",
        "expected_strength": "reviewable_execution_plan",
        "required_evidence": ["risk_summary", "test_plan", "review_need"],
    },
    {
        "id": "compose-diagnostics",
        "title": "Start/Deploy diagnostizieren",
        "use_case": "Start/Deploy diagnostizieren",
        "goal": "Diagnostiziere einen Compose- oder Health-Check-Fehler und schlage sichere naechste Schritte vor.",
        "expected_strength": "operations_diagnostics",
        "required_evidence": ["diagnostic_steps", "blocked_or_safe_boundary", "operator_next_step"],
    },
    {
        "id": "change-review",
        "title": "Change Review",
        "use_case": "Change Review",
        "goal": "Pruefe eine Aenderung auf Risiken, fehlende Tests und Governance-Auswirkungen.",
        "expected_strength": "governance_visible_review",
        "required_evidence": ["risk_findings", "test_gaps", "policy_or_review_signal"],
    },
    {
        "id": "guided-first-run",
        "title": "Gefuehrte Goal-Erstellung",
        "use_case": "Gefuehrte Goal-Erstellung fuer Erstnutzer",
        "goal": "Fuehre einen Erstnutzer von Ziel zu Plan, Aufgaben und sichtbarem naechstem Schritt.",
        "expected_strength": "first_run_clarity",
        "required_evidence": ["success_signal", "created_tasks", "next_action"],
    },
]

BENCHMARK_CRITERIA: list[dict[str, Any]] = [
    {"id": "task_success", "label": "Task Success", "weight": 20, "description": "Produces a usable result for the stated goal."},
    {"id": "time_to_signal", "label": "Time To Signal", "weight": 10, "description": "Reaches first useful plan/result quickly."},
    {"id": "traceability", "label": "Traceability", "weight": 15, "description": "Connects goal, plan, tasks, verification and artifacts."},
    {"id": "governance_quality", "label": "Governance Quality", "weight": 20, "description": "Explains review, policy and safe boundaries instead of hiding them."},
    {"id": "block_quality", "label": "Block Quality", "weight": 10, "description": "Blocks unsafe or underspecified work with clear reasons and next steps."},
    {"id": "result_value", "label": "Result Value", "weight": 15, "description": "Leads with user value before internal mechanics."},
    {"id": "reproducibility", "label": "Reproducibility", "weight": 10, "description": "Can be repeated with comparable inputs, outputs and evidence."},
]

COMPARISON_TARGETS: list[dict[str, Any]] = [
    {
        "id": "openhands-like",
        "label": "OpenHands-like",
        "comparison_focus": ["autonomous_coding", "tool_execution", "developer_loop"],
        "expected_contrast": "Ananta should emphasize hub-owned governance, traceability and review signals.",
    },
    {
        "id": "opendevin-like",
        "label": "OpenDevin-like",
        "comparison_focus": ["issue_to_code_flow", "sandbox_execution", "iteration_speed"],
        "expected_contrast": "Ananta should show stronger task ownership, policy visibility and artifact traceability.",
    },
    {
        "id": "openclaw-like",
        "label": "OpenClaw-like",
        "comparison_focus": ["agent_tool_use", "task_execution", "local_workflow"],
        "expected_contrast": "Ananta should make safety boundaries, blocked states and next actions more visible.",
    },
]

RELEASE_NARRATIVE_FIELDS: list[dict[str, Any]] = [
    {"id": "headline", "description": "One-sentence release benchmark outcome."},
    {"id": "best_signal", "description": "Strongest measured improvement or preserved strength."},
    {"id": "governance_signal", "description": "Review, block or safety evidence worth highlighting."},
    {"id": "regression_watch", "description": "Weakest benchmark dimension or follow-up risk."},
    {"id": "evidence_links", "description": "CI, artifact, run log or release evidence links."},
]


def build_product_benchmark_suite() -> dict[str, Any]:
    return {
        "version": BENCHMARK_SUITE_VERSION,
        "tasks": [dict(task) for task in BENCHMARK_TASKS],
        "criteria": [dict(criterion) for criterion in BENCHMARK_CRITERIA],
        "comparison_targets": [dict(target) for target in COMPARISON_TARGETS],
        "release_narrative_fields": [dict(field) for field in RELEASE_NARRATIVE_FIELDS],
        "score_total": sum(int(criterion["weight"]) for criterion in BENCHMARK_CRITERIA),
        "comparison_rule": "Compare only runs with the same task id, profile, governance mode and evidence level.",
    }
