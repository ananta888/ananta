from __future__ import annotations

from typing import Any

from worker.shell.command_policy import classify_command


def build_command_plan_artifact(
    *,
    task_id: str,
    capability_id: str,
    command: str,
    explanation: str,
    expected_effects: list[str],
    working_directory: str = ".",
    policy: dict[str, Any],
    hub_policy_decision: str = "allow",
) -> dict[str, Any]:
    normalized_command = str(command).strip()
    normalized_explanation = str(explanation).strip()
    if not normalized_command or not normalized_explanation:
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "command": normalized_command or "echo '<missing command>'",
            "explanation": normalized_explanation or "Malformed request: command or explanation missing.",
            "risk_classification": "critical",
            "required_approval": True,
            "working_directory": str(working_directory).strip() or ".",
            "expected_effects": ["No execution; request rejected as malformed."],
        }
    decision = classify_command(command=normalized_command, policy=policy, hub_policy_decision=hub_policy_decision)
    return {
        "schema": "command_plan_artifact.v1",
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "command": normalized_command,
        "explanation": normalized_explanation,
        "risk_classification": decision.risk_classification,
        "required_approval": decision.required_approval,
        "working_directory": str(working_directory).strip() or ".",
        "expected_effects": [str(item).strip() for item in expected_effects if str(item).strip()],
    }
