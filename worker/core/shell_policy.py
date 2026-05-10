"""Shell command policy: planning vs execution split and workspace enforcement.

EW-T015: command_plan produces CommandPlanArtifact only.
          command_execute requires shell_execute capability + approval when confirm_required.
          Commands outside workspace or environment constraints are refused.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Unsafe command patterns (deny-list) ───────────────────────────────────────

_UNSAFE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b"),        # rm -rf
    re.compile(r"\bmkfs\b"),                                 # format filesystem
    re.compile(r"\bdd\s+.*of=/dev/"),                        # dd to block device
    re.compile(r"\bchmod\s+[0-9]*7[0-9]*\s+/"),             # world-writable root paths
    re.compile(r"\bcurl\b.*\|\s*(bash|sh|zsh)\b"),           # pipe-to-shell download
    re.compile(r"\bwget\b.*-O\s*-.*\|\s*(bash|sh|zsh)\b"),  # wget pipe-to-shell
    re.compile(r"\bsudo\b"),                                 # privilege escalation
    re.compile(r"\bsu\s+-"),                                 # switch user
    re.compile(r">\s*/dev/(sd|nvme|hd)[a-z]"),              # write to block device
    re.compile(r"\bkill\s+-9\s+1\b"),                        # kill init
    re.compile(r"\bpkill\s+.*-9\b"),                         # mass kill
    re.compile(r"\biptables\b"),                             # firewall manipulation
    re.compile(r"\bnftables?\b"),                            # firewall manipulation
]

_WORKSPACE_ESCAPE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.\./\.\./"),   # multiple levels of ../ traversal
    re.compile(r"~[/\\]"),       # home directory escape
]


# ── CommandPlanArtifact ───────────────────────────────────────────────────────

@dataclass
class PlannedCommand:
    step: int
    command: str
    cwd: str = ""
    rationale: str = ""
    side_effects: list[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass
class CommandPlanArtifact:
    """Produced by plan_shell — no execution occurs. EW-T015."""
    task_id: str
    goal: str
    workspace_root: str
    steps: list[PlannedCommand] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "command_plan_artifact",
            "task_id": self.task_id,
            "goal": self.goal,
            "workspace_root": self.workspace_root,
            "steps": [
                {
                    "step": s.step,
                    "command": s.command,
                    "cwd": s.cwd or self.workspace_root,
                    "rationale": s.rationale,
                    "side_effects": s.side_effects,
                    "requires_approval": s.requires_approval,
                }
                for s in self.steps
            ],
            "warnings": self.warnings,
        }


# ── ShellPolicyResult ─────────────────────────────────────────────────────────

@dataclass
class ShellPolicyResult:
    allowed: bool
    reason_code: str
    detail: str = ""


# ── ShellPolicy ───────────────────────────────────────────────────────────────

class ShellPolicy:
    """Validates shell commands against workspace scope and unsafe patterns.

    Always called before any shell execution — plan or execute.
    """

    def check_command(
        self,
        command: str,
        *,
        workspace_root: str,
        cwd: str = "",
    ) -> ShellPolicyResult:
        """Returns allowed=True only if command is safe and within scope."""
        if not command or not command.strip():
            return ShellPolicyResult(False, "shell_command_unsafe", "empty command")

        if self._matches_unsafe(command):
            return ShellPolicyResult(
                False, "shell_command_unsafe",
                f"command matches unsafe pattern: {command[:80]!r}",
            )

        effective_cwd = cwd or workspace_root
        if workspace_root and not self._within_workspace(command, workspace_root, effective_cwd):
            return ShellPolicyResult(
                False, "tool_scope_violation",
                f"command references path outside workspace {workspace_root!r}",
            )

        return ShellPolicyResult(True, "shell_allow")

    def check_cwd(self, cwd: str, workspace_root: str) -> ShellPolicyResult:
        """Verify that the working directory is within workspace_root."""
        if not workspace_root:
            return ShellPolicyResult(True, "no_workspace_constraint")
        try:
            cwd_resolved = Path(cwd).resolve()
            root_resolved = Path(workspace_root).resolve()
            cwd_resolved.relative_to(root_resolved)
            return ShellPolicyResult(True, "cwd_ok")
        except ValueError:
            return ShellPolicyResult(
                False, "tool_scope_violation",
                f"cwd {cwd!r} is outside workspace {workspace_root!r}",
            )

    def classify_side_effects(self, command: str) -> list[str]:
        """Heuristic classification of side effects for CommandPlanArtifact."""
        effects: list[str] = []
        cmd = command.lower()
        if any(kw in cmd for kw in [">", "tee ", "write", "cp ", "mv ", "mkdir", "touch"]):
            effects.append("filesystem_write")
        if any(kw in cmd for kw in ["curl ", "wget ", "git clone", "pip install", "apt "]):
            effects.append("network")
        if any(kw in cmd for kw in ["systemctl", "service ", "kill ", "pkill"]):
            effects.append("process")
        if any(kw in cmd for kw in ["docker ", "podman "]):
            effects.append("container")
        return effects

    def build_plan_artifact(
        self,
        *,
        task_id: str,
        goal: str,
        commands: list[str],
        workspace_root: str,
    ) -> CommandPlanArtifact:
        """Build a CommandPlanArtifact from a list of raw command strings."""
        artifact = CommandPlanArtifact(
            task_id=task_id,
            goal=goal,
            workspace_root=workspace_root,
        )
        for i, cmd in enumerate(commands, start=1):
            result = self.check_command(cmd, workspace_root=workspace_root)
            requires_approval = not result.allowed or bool(self.classify_side_effects(cmd))
            artifact.steps.append(PlannedCommand(
                step=i,
                command=cmd,
                cwd=workspace_root,
                side_effects=self.classify_side_effects(cmd),
                requires_approval=requires_approval,
            ))
            if not result.allowed:
                artifact.warnings.append(f"step {i}: {result.detail}")
        return artifact

    # ── Internals ──────────────────────────────────────────────────────────────

    def _matches_unsafe(self, command: str) -> bool:
        for pattern in _UNSAFE_PATTERNS:
            if pattern.search(command):
                return True
        return False

    def _within_workspace(self, command: str, workspace_root: str, cwd: str) -> bool:
        """Heuristic: check if absolute paths in command are inside workspace_root."""
        try:
            root = Path(workspace_root).resolve()
        except Exception:
            return True  # can't resolve → don't block, other checks cover it

        # Check for workspace escape patterns
        for pat in _WORKSPACE_ESCAPE_PATTERNS:
            if pat.search(command):
                return False

        # Check any absolute paths mentioned in command tokens
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        for token in tokens:
            token = token.strip("'\"")
            if token.startswith("/") and not token.startswith("/dev/") and not token.startswith("/proc/"):
                try:
                    Path(token).resolve().relative_to(root)
                except ValueError:
                    return False
        return True
