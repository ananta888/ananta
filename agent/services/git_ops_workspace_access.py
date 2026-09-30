"""Workspace resolution, Git command execution and value helpers for Git Ops.

The collaborators here are the lowest layer of the Git Ops service family:
they know how to run a hardened ``git`` subprocess inside a registered
workspace and how to normalize client supplied paths. They carry no
read-model, policy or audit knowledge (SRP).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import urlsplit, urlunsplit

from agent.services.git_remote_policy_service import hardened_git_environment
from agent.services.ops_command_runner import CommandResult, CommandRunner
from agent.services.ops_models import OpsError
from agent.services.ops_registry_service import WorkspaceRef

CONFLICT_STATES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")
MAX_MUTATION_PATHS = 500


class WorkspaceRegistryPort(Protocol):
    """The subset of the Ops registry that Git Ops depends on."""

    def workspaces(self) -> Iterable[WorkspaceRef]: ...

    def resolve_workspace(self, workspace_id: str | None) -> WorkspaceRef | None: ...

    def resolve_relative_path(self, workspace_id: str | None, path: str | None) -> Path | None: ...


class GitCommandExecutor:
    """Runs ``git`` with the hardened environment through a command runner."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def available(self) -> bool:
        return bool(self._runner.exists("git"))

    def run(self, args: list[str], *, cwd: Path, timeout_seconds: int | None = None) -> CommandResult:
        return self._runner.run(
            ["git", *args],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=hardened_git_environment(),
        )


class GitWorkspaceAccess:
    """Resolves registered workspaces and validates workspace-relative paths."""

    def __init__(self, *, registry: WorkspaceRegistryPort, git: GitCommandExecutor) -> None:
        self._registry = registry
        self._git = git

    def registered_workspaces(self) -> Iterable[WorkspaceRef]:
        return self._registry.workspaces()

    def resolve(self, workspace_id: str | None) -> tuple[WorkspaceRef | None, OpsError | None]:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return None, OpsError("workspace_not_allowed", "workspace not registered")
        return workspace, None

    def validate_repository(self, workspace: WorkspaceRef) -> OpsError | None:
        if not self._git.available():
            return OpsError("git_not_found", "git binary not found")
        inside = self._git.run(["rev-parse", "--is-inside-work-tree"], cwd=workspace.root)
        if inside.timed_out:
            return OpsError("git_timeout", "git repository check timed out")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return OpsError("git_not_repository", "workspace is not a git repository")
        return None

    def is_repository(self, root: Path) -> bool:
        if not self._git.available():
            return False
        result = self._git.run(["rev-parse", "--is-inside-work-tree"], cwd=root)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def relative_path(self, workspace: WorkspaceRef, path: str | None) -> tuple[str, OpsError | None]:
        raw = str(path or "").strip()
        if not raw or Path(raw).is_absolute():
            return "", OpsError("path_not_allowed", "workspace-relative path required")
        resolved = self._registry.resolve_relative_path(workspace.workspace_id, raw)
        if resolved is None:
            return "", OpsError("path_not_allowed", "path escapes workspace")
        return resolved.relative_to(workspace.root).as_posix(), None

    def mutation_paths(
        self, workspace_id: str | None, paths: Iterable[str]
    ) -> tuple[WorkspaceRef | None, list[str], OpsError | None]:
        workspace, error = self.resolve(workspace_id)
        if error:
            return None, [], error
        assert workspace is not None
        validation = self.validate_repository(workspace)
        if validation:
            return workspace, [], validation
        selected: list[str] = []
        for value in paths:
            normalized, path_error = self.relative_path(workspace, str(value or ""))
            if path_error:
                return workspace, [], path_error
            if normalized not in selected:
                selected.append(normalized)
        if not selected:
            return workspace, [], OpsError("path_not_allowed", "explicit paths required")
        if len(selected) > MAX_MUTATION_PATHS:
            return workspace, [], OpsError("path_not_allowed", "too many paths in one Git action")
        return workspace, selected, None


def literal_pathspec(path: str) -> str:
    return f":(literal){path}"


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def safe_git_message(value: str) -> str:
    text = str(value or "Git command failed").replace("\x00", "").strip()
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1***@", text)
    text = re.sub(r"(?i)(token|password|authorization)=([^&\s]+)", r"\1=***", text)
    return text[:240]


def redact_remote_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        pass
    safe = raw.split("?", 1)[0].split("#", 1)[0]
    if "@" in safe and ":" in safe.split("@", 1)[0] and not safe.startswith("git@"):
        safe = f"***@{safe.split('@', 1)[1]}"
    return safe


def upstream_remote_name(upstream: str, configured_remotes: Iterable[str]) -> str:
    """Return the longest configured remote name that prefixes ``upstream``."""

    return next(
        (name for name in sorted(configured_remotes, key=len, reverse=True) if upstream.startswith(f"{name}/")),
        "",
    )
