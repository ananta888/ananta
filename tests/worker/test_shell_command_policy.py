from __future__ import annotations

from worker.shell.command_policy import classify_command


POLICY = {
    "allowlist": ["echo", "python", "pytest"],
    "approval_required_commands": ["pip", "npm"],
    "denylist_tokens": ["rm -rf /", "mkfs"],
}


def test_shell_policy_allowlist_classifies_safe() -> None:
    decision = classify_command(command="echo hi", policy=POLICY)
    assert decision.classification == "safe"
    assert decision.required_approval is False


def test_shell_policy_denylist_classifies_denied() -> None:
    decision = classify_command(command="mkfs /dev/sda", policy=POLICY)
    assert decision.classification == "denied"
    assert decision.required_approval is True


def test_shell_policy_unknown_command_classified_unknown() -> None:
    decision = classify_command(command="customcmd --flag", policy=POLICY)
    assert decision.classification == "unknown"
    assert decision.required_approval is True


def test_shell_policy_path_escape_is_denied() -> None:
    decision = classify_command(command="cat ../secret.txt", policy=POLICY)
    assert decision.classification == "denied"


def test_shell_policy_cannot_loosen_hub_approval_requirement() -> None:
    decision = classify_command(command="echo hi", policy=POLICY, hub_policy_decision="approval_required")
    assert decision.classification == "approval_required"
    assert decision.required_approval is True


def test_shell_policy_balanced_auto_allows_readonly_git_diagnostic() -> None:
    decision = classify_command(
        command="git status",
        policy=POLICY,
        hub_policy_decision="allow",
        execution_profile="balanced",
    )
    assert decision.classification == "safe"
    assert decision.required_approval is False


def test_shell_policy_safe_profile_keeps_unknown_command_guarded() -> None:
    decision = classify_command(
        command="git status",
        policy=POLICY,
        hub_policy_decision="allow",
        execution_profile="safe",
    )
    assert decision.classification == "unknown"
    assert decision.required_approval is True
