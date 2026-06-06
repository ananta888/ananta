"""SCTR-002: Security policy for SnakeChat direct tools and context preparation.

Enforces:
- No write/execute operations from tool dispatch
- No cross-workspace path traversal
- Context sanitization before sending to /snake/ask
- Denied extension/pattern lists for FilesystemReadTool
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Patterns that indicate secrets — stripped from LLM context
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'[A-Z][A-Z0-9_]{3,}=\S{6,}'),        # ENV_VAR=value
    re.compile(r'-----BEGIN [A-Z ]+-----'),             # PEM header
    re.compile(r'(?:sk-|ghp_|ghs_|xoxb-|ya29\.)\S{10,}'),  # known prefixes
    re.compile(r'"(?:password|secret|token|api_key)"\s*:\s*"[^"]{6,}"', re.I),
    re.compile(r"(?:password|secret|token)\s*=\s*['\"]\S{6,}['\"]", re.I),
]

_REDACTION_PLACEHOLDER = "[REDACTED]"


@dataclass
class SnakeChatSecurityPolicy:
    workspace_root: str = ""
    allowed_read_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".ts", ".tsx", ".js", ".jsx",
        ".go", ".rs", ".java", ".cs", ".cpp", ".c", ".h",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".md", ".txt", ".sh", ".bash", ".sql",
        ".html", ".css", ".scss", ".lock",
    ])
    denied_path_patterns: list[str] = field(default_factory=lambda: [
        ".env", ".env.*", "secrets.*", "*.key", "*.pem",
        "*.p12", "*.pfx", "id_rsa", "id_ed25519", "credentials.*",
        "token.txt", "token.json",
    ])
    max_file_bytes_display: int = 64 * 1024  # 64 KB for inline chat display
    sanitize_context_before_llm: bool = True
    allow_git_read: bool = True
    allow_todo_read: bool = True
    allow_filesystem_read: bool = True
    allow_llm_bypass: bool = False  # direct LLM call without hub routing


def sanitize_context_for_llm(text: str, *, policy: SnakeChatSecurityPolicy | None = None) -> str:
    """Strip secret-looking strings from text before sending to an LLM endpoint."""
    if policy and not policy.sanitize_context_before_llm:
        return text
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTION_PLACEHOLDER, result)
    return result


def check_path_allowed(
    relative_path: str,
    *,
    policy: SnakeChatSecurityPolicy,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason). Used by FilesystemReadTool before any read.
    Does NOT do I/O — purely string-based checks.
    """
    import fnmatch
    normalized = relative_path.replace("\\", "/").lstrip("/")

    # Traversal check
    if ".." in normalized.split("/"):
        return False, "path_traversal_denied"

    filename = normalized.split("/")[-1]
    for pattern in policy.denied_path_patterns:
        if fnmatch.fnmatch(filename, pattern):
            return False, f"denied_pattern:{pattern}"

    # Extension check
    ext = "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized else ""
    allowed_exts = [e.lower() for e in policy.allowed_read_extensions]
    if allowed_exts and ext and ext not in allowed_exts:
        return False, f"extension_denied:{ext}"

    return True, "allowed"


def check_tool_dispatch_allowed(
    route: str,
    *,
    policy: SnakeChatSecurityPolicy,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason) for a routing decision.
    Prevents write/execute routes from being dispatched.
    """
    _WRITE_ROUTES = {
        "write_file", "delete_file", "execute_command", "shell_exec",
        "modify_git", "git_commit", "git_push",
    }
    if route in _WRITE_ROUTES:
        return False, f"write_route_denied:{route}"
    if route == "llm_bypass" and not policy.allow_llm_bypass:
        return False, "llm_bypass_not_allowed"
    if route == "filesystem_read" and not policy.allow_filesystem_read:
        return False, "filesystem_read_disabled"
    if route == "git_read" and not policy.allow_git_read:
        return False, "git_read_disabled"
    if route == "todo_read" and not policy.allow_todo_read:
        return False, "todo_read_disabled"
    return True, "allowed"
