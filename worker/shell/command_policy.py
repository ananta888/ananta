from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ABSOLUTE_RM_PATTERN = re.compile(r"\brm\s+-rf\s+/\b")
_FORK_BOMB_PATTERN = re.compile(r":\s*\(\)\s*\{")


@dataclass(frozen=True)
class CommandPolicyDecision:
    classification: str
    reason: str
    required_approval: bool
    risk_classification: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "reason": self.reason,
            "required_approval": self.required_approval,
            "risk_classification": self.risk_classification,
        }


def load_shell_command_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _risk_for_classification(classification: str) -> str:
    normalized = str(classification or "").strip().lower()
    if normalized == "safe":
        return "low"
    if normalized == "unknown":
        return "medium"
    if normalized == "approval_required":
        return "high"
    return "critical"


def classify_command(
    *,
    command: str,
    policy: dict[str, Any],
    hub_policy_decision: str = "allow",
) -> CommandPolicyDecision:
    cmd = str(command or "").strip()
    if not cmd:
        classification = "unknown"
        return CommandPolicyDecision(classification, "empty_command", True, _risk_for_classification(classification))
    hub_decision = str(hub_policy_decision or "").strip().lower()
    if hub_decision == "deny":
        classification = "denied"
        return CommandPolicyDecision(classification, "hub_policy_denied", True, _risk_for_classification(classification))
    if _ABSOLUTE_RM_PATTERN.search(cmd) or _FORK_BOMB_PATTERN.search(cmd):
        classification = "denied"
        return CommandPolicyDecision(classification, "dangerous_pattern", True, _risk_for_classification(classification))

    allowlist = {str(item).strip() for item in list(policy.get("allowlist") or []) if str(item).strip()}
    approval_required = {
        str(item).strip() for item in list(policy.get("approval_required_commands") or []) if str(item).strip()
    }
    denylist_tokens = {str(item).strip() for item in list(policy.get("denylist_tokens") or []) if str(item).strip()}

    parts = shlex.split(cmd)
    if not parts:
        classification = "unknown"
        return CommandPolicyDecision(classification, "empty_command", True, _risk_for_classification(classification))
    binary = parts[0]

    for token in denylist_tokens:
        if token and token in cmd:
            classification = "denied"
            return CommandPolicyDecision(classification, "denylist_token", True, _risk_for_classification(classification))
    if any(".." in arg for arg in parts[1:]):
        classification = "denied"
        return CommandPolicyDecision(classification, "path_escape_detected", True, _risk_for_classification(classification))

    if binary in approval_required:
        classification = "approval_required"
        return CommandPolicyDecision(
            classification, "command_in_approval_required_set", True, _risk_for_classification(classification)
        )

    if binary in allowlist:
        if hub_decision == "approval_required":
            classification = "approval_required"
            return CommandPolicyDecision(classification, "hub_requires_approval", True, _risk_for_classification(classification))
        classification = "safe"
        return CommandPolicyDecision(classification, "command_allowlisted", False, _risk_for_classification(classification))

    if hub_decision == "approval_required":
        classification = "approval_required"
        return CommandPolicyDecision(classification, "hub_requires_approval", True, _risk_for_classification(classification))
    classification = "unknown"
    return CommandPolicyDecision(classification, "command_not_classified", True, _risk_for_classification(classification))
