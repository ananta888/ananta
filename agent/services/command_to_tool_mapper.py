from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandToolMapping:
    original_command: str
    mapped_tool: str | None
    args: dict
    reason: str
    confidence: str = "explicit_rule"


class CommandToToolMapper:
    """Conservative mapping from simple shell commands to explicit tools."""

    def map(self, command: str) -> CommandToolMapping:
        raw = str(command or "").strip()
        if not raw:
            return CommandToolMapping(original_command=raw, mapped_tool=None, args={}, reason="empty")

        # Keep mapper strict; any complex shell syntax remains unmapped.
        if any(token in raw for token in ("|", ">", "<", "&", ";", "$(", "`")):
            return CommandToolMapping(original_command=raw, mapped_tool=None, args={}, reason="complex_shell_syntax")

        parts = raw.split()
        head = parts[0] if parts else ""
        if head == "pytest":
            return CommandToolMapping(original_command=raw, mapped_tool="run_tests", args={"command": raw}, reason="pytest_to_run_tests")
        if raw == "git status":
            return CommandToolMapping(original_command=raw, mapped_tool="git_status", args={}, reason="git_status_rule")
        if raw == "git diff":
            return CommandToolMapping(original_command=raw, mapped_tool="git_diff", args={}, reason="git_diff_rule")
        if head == "cat" and len(parts) == 2:
            return CommandToolMapping(original_command=raw, mapped_tool="file_read", args={"path": parts[1]}, reason="cat_to_file_read")
        if head == "ls":
            path = parts[1] if len(parts) > 1 and not parts[1].startswith("-") else "."
            return CommandToolMapping(original_command=raw, mapped_tool="file_list", args={"path": path}, reason="ls_to_file_list")

        return CommandToolMapping(original_command=raw, mapped_tool=None, args={}, reason="no_mapping_rule")

