from __future__ import annotations

from worker.shell.command_planner import build_command_plan_artifact


POLICY = {
    "allowlist": ["echo", "python", "pytest"],
    "approval_required_commands": ["pip", "npm"],
    "denylist_tokens": ["rm -rf /"],
}


def test_command_planner_generates_safe_command_plan() -> None:
    artifact = build_command_plan_artifact(
        task_id="T1",
        capability_id="worker.command.plan",
        command="echo hello",
        explanation="Print greeting",
        expected_effects=["Writes greeting to stdout."],
        policy=POLICY,
    )
    assert artifact["schema"] == "command_plan_artifact.v1"
    assert artifact["command_hash"]
    assert artifact["required_approval"] is False
    assert artifact["risk_classification"] == "low"


def test_command_planner_marks_risky_command_as_approval_required() -> None:
    artifact = build_command_plan_artifact(
        task_id="T1",
        capability_id="worker.command.plan",
        command="pip install x",
        explanation="Install dependency",
        expected_effects=["Changes environment."],
        policy=POLICY,
    )
    assert artifact["required_approval"] is True
    assert artifact["risk_classification"] == "high"


def test_command_planner_handles_malformed_request() -> None:
    artifact = build_command_plan_artifact(
        task_id="T1",
        capability_id="worker.command.plan",
        command="",
        explanation="",
        expected_effects=[],
        policy=POLICY,
    )
    assert artifact["required_approval"] is True
    assert artifact["risk_classification"] == "critical"
