from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


OpsErrorCode = Literal[
    "git_not_found",
    "git_not_repository",
    "git_timeout",
    "git_command_failed",
    "invalid_commit_message",
    "docker_not_found",
    "docker_unreachable",
    "docker_permission_denied",
    "docker_boundary_not_configured",
    "compose_plugin_missing",
    "compose_file_invalid",
    "compose_project_not_registered",
    "workspace_not_allowed",
    "path_not_allowed",
    "approval_required",
    "policy_denied",
    "output_truncated",
]


READ_ACTIONS = frozenset({"status", "diff", "list", "logs", "inspect_light", "config"})
MUTATING_ACTIONS = frozenset(
    {"stage", "unstage", "commit", "fetch", "pull_ff_only", "push", "start", "stop", "restart", "up", "down"}
)
DANGEROUS_ACTIONS = frozenset(
    {"force_push", "reset_hard", "clean", "rm_volume", "prune", "compose_down_volumes", "docker_socket_mount_enable"}
)


@dataclass(frozen=True)
class SerializableDto:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OpsError(SerializableDto):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpsActionResult(SerializableDto):
    ok: bool
    action: str
    target_id: str = ""
    decision: str = "allow"
    error: OpsError | None = None
    approval_id: str | None = None
    audit_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GitChangedFile(SerializableDto):
    path: str
    index_status: str = ""
    worktree_status: str = ""
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False


@dataclass(frozen=True)
class GitCommitSummary(SerializableDto):
    sha: str
    subject: str


@dataclass(frozen=True)
class GitStatus(SerializableDto):
    workspace_id: str
    branch: str = ""
    upstream: str = ""
    remote_name: str = ""
    dirty: bool = False
    changed_files: list[GitChangedFile] = field(default_factory=list)
    recent_commits: list[GitCommitSummary] = field(default_factory=list)
    error: OpsError | None = None


@dataclass(frozen=True)
class GitDiff(SerializableDto):
    workspace_id: str
    cached: bool = False
    path: str = ""
    diff: str = ""
    truncated: bool = False
    error: OpsError | None = None


@dataclass(frozen=True)
class DockerEngineStatus(SerializableDto):
    available: bool
    boundary: str = "disabled"
    docker_version: str = ""
    compose_available: bool = False
    platform_hint: str = ""
    error: OpsError | None = None


@dataclass(frozen=True)
class DockerContainerSummary(SerializableDto):
    id: str
    name: str
    image: str
    status: str
    health: str = ""
    ports: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    compose_project: str = ""
    uptime: str = ""


@dataclass(frozen=True)
class ComposeServiceStatus(SerializableDto):
    name: str
    state: str = ""
    health: str = ""
    exit_code: str = ""
    ports: str = ""


@dataclass(frozen=True)
class ComposeProjectSummary(SerializableDto):
    project_id: str
    name: str
    project_directory: str
    compose_files: list[str]
    profiles: list[str] = field(default_factory=list)
    marker: str = "preferred"
    category: str = "dev"
    allowed_actions: list[str] = field(default_factory=list)
    services: list[ComposeServiceStatus] = field(default_factory=list)
    error: OpsError | None = None
