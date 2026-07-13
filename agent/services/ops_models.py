from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

OpsErrorCode = Literal[
    "git_not_found",
    "git_not_repository",
    "git_timeout",
    "git_command_failed",
    "git_detached_head",
    "git_dirty_worktree",
    "git_conflict",
    "git_operation_in_progress",
    "git_no_upstream",
    "git_remote_not_allowed",
    "git_branch_not_allowed",
    "git_path_state_invalid",
    "git_untracked_discard_denied",
    "git_nothing_to_commit",
    "invalid_commit_message",
    "docker_not_found",
    "docker_unreachable",
    "docker_permission_denied",
    "docker_boundary_not_configured",
    "docker_container_not_registered",
    "compose_plugin_missing",
    "compose_file_invalid",
    "compose_project_not_registered",
    "workspace_not_allowed",
    "path_not_allowed",
    "approval_required",
    "policy_denied",
    "output_truncated",
]


READ_ACTIONS = frozenset(
    {
        "status",
        "diff",
        "changes",
        "history",
        "branches",
        "remotes",
        "activity",
        "list",
        "logs",
        "inspect_light",
        "stats",
        "info",
        "config",
    }
)
MUTATING_ACTIONS = frozenset(
    {
        "stage",
        "unstage",
        "discard",
        "commit",
        "fetch",
        "pull_ff_only",
        "push",
        "start",
        "stop",
        "restart",
        "pull",
        "up",
        "down",
    }
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
    original_path: str = ""
    index_status: str = ""
    worktree_status: str = ""
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False
    conflicted: bool = False
    renamed: bool = False
    deleted: bool = False
    binary: bool = False
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class GitCommitSummary(SerializableDto):
    sha: str
    subject: str
    short_sha: str = ""
    author_name: str = ""
    author_email: str = ""
    authored_at: str = ""
    parents: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitStatus(SerializableDto):
    workspace_id: str
    branch: str = ""
    head_sha: str = ""
    upstream: str = ""
    remote_name: str = ""
    detached: bool = False
    ahead: int = 0
    behind: int = 0
    operation_state: str = "idle"
    dirty: bool = False
    conflict_count: int = 0
    staged_count: int = 0
    unstaged_count: int = 0
    untracked_count: int = 0
    can_commit: bool = False
    can_pull: bool = False
    can_push: bool = False
    truncated: bool = False
    changed_files: list[GitChangedFile] = field(default_factory=list)
    recent_commits: list[GitCommitSummary] = field(default_factory=list)
    error: OpsError | None = None


@dataclass(frozen=True)
class GitDiffStat(SerializableDto):
    path: str
    additions: int = 0
    deletions: int = 0
    binary: bool = False


@dataclass(frozen=True)
class GitDiff(SerializableDto):
    workspace_id: str
    cached: bool = False
    path: str = ""
    scope: str = "unstaged"
    head_sha: str = ""
    diff: str = ""
    staged_diff: str = ""
    unstaged_diff: str = ""
    untracked_diff: str = ""
    stats: list[GitDiffStat] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0
    truncated: bool = False
    error: OpsError | None = None


@dataclass(frozen=True)
class GitChanges(SerializableDto):
    workspace_id: str
    items: list[GitChangedFile] = field(default_factory=list)
    count: int = 0
    staged_count: int = 0
    unstaged_count: int = 0
    untracked_count: int = 0
    conflict_count: int = 0
    truncated: bool = False
    error: OpsError | None = None


@dataclass(frozen=True)
class GitHistory(SerializableDto):
    workspace_id: str
    items: list[GitCommitSummary] = field(default_factory=list)
    count: int = 0
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    error: OpsError | None = None


@dataclass(frozen=True)
class GitBranch(SerializableDto):
    name: str
    current: bool = False
    remote: bool = False
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    sha: str = ""
    last_commit_sha: str = ""
    last_commit_subject: str = ""
    last_commit_at: str = ""


@dataclass(frozen=True)
class GitBranches(SerializableDto):
    workspace_id: str
    items: list[GitBranch] = field(default_factory=list)
    count: int = 0
    error: OpsError | None = None


@dataclass(frozen=True)
class GitRemote(SerializableDto):
    name: str
    fetch_url: str = ""
    push_url: str = ""


@dataclass(frozen=True)
class GitRemotes(SerializableDto):
    workspace_id: str
    items: list[GitRemote] = field(default_factory=list)
    count: int = 0
    error: OpsError | None = None


@dataclass(frozen=True)
class GitActivityEvent(SerializableDto):
    id: str
    timestamp: str
    actor: str
    action: str
    operation: str = ""
    outcome: str = "unknown"
    source: str = "ops"
    workspace_id: str = ""
    task_id: str = ""
    goal_id: str = ""
    trace_id: str = ""
    approval_id: str = ""
    summary: str = ""


@dataclass(frozen=True)
class GitActivity(SerializableDto):
    workspace_id: str
    items: list[GitActivityEvent] = field(default_factory=list)
    count: int = 0
    error: OpsError | None = None


@dataclass(frozen=True)
class DockerEngineStatus(SerializableDto):
    available: bool
    boundary: str = "disabled"
    docker_version: str = ""
    compose_available: bool = False
    platform_hint: str = ""
    engine: dict[str, Any] = field(default_factory=dict)
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
    state: str = ""
    command: str = ""
    created_at: str = ""
    size: str = ""
    networks: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    registered: bool = True
    managed: bool = False
    allowed_actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComposeServiceStatus(SerializableDto):
    name: str
    state: str = ""
    health: str = ""
    exit_code: str = ""
    ports: str = ""
    container_id: str = ""
    image: str = ""
    command: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class ComposeProjectSummary(SerializableDto):
    project_id: str
    name: str
    project_directory: str
    compose_files: list[str]
    profiles: list[str] = field(default_factory=list)
    available_profiles: list[str] = field(default_factory=list)
    marker: str = "preferred"
    category: str = "dev"
    allowed_actions: list[str] = field(default_factory=list)
    services: list[ComposeServiceStatus] = field(default_factory=list)
    error: OpsError | None = None
