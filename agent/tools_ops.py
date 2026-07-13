from __future__ import annotations

from agent.services.docker_compose_service import get_docker_compose_service
from agent.services.docker_engine_service import get_docker_engine_service
from agent.services.git_ops_service import get_git_ops_service
from agent.tools_registry import registry


@registry.register(
    name="git.workspaces",
    description="List the opaque workspace identifiers available to the hub-side Git control surface.",
    parameters={"type": "object", "properties": {}},
)
def ops_git_workspaces():
    items = get_git_ops_service().workspaces()
    return {"items": items, "count": len(items)}


@registry.register(
    name="git.status",
    description="Read-only Git status for a registered workspace.",
    parameters={"type": "object", "properties": {"workspace_id": {"type": "string", "default": "repo"}}},
)
def ops_git_status(workspace_id: str = "repo"):
    return get_git_ops_service().status(workspace_id).to_dict()


@registry.register(
    name="git.diff",
    description="Read-only capped Git diff for a registered workspace.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "path": {"type": "string"},
            "cached": {"type": "boolean", "default": False},
            "scope": {"type": "string", "enum": ["staged", "unstaged", "combined"]},
        },
    },
)
def ops_git_diff(
    workspace_id: str = "repo",
    path: str | None = None,
    cached: bool = False,
    scope: str | None = None,
):
    return get_git_ops_service().diff(workspace_id, path=path, cached=cached, scope=scope).to_dict()


@registry.register(
    name="git.changes",
    description="List structured staged, unstaged, untracked and conflicted paths for a registered workspace.",
    parameters={"type": "object", "properties": {"workspace_id": {"type": "string", "default": "repo"}}},
)
def ops_git_changes(workspace_id: str = "repo"):
    return get_git_ops_service().changes(workspace_id).to_dict()


@registry.register(
    name="git.history",
    description="Read bounded commit history for a registered workspace.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
)
def ops_git_history(workspace_id: str = "repo", limit: int = 50, offset: int = 0):
    return get_git_ops_service().history(workspace_id, limit=limit, offset=offset).to_dict()


@registry.register(
    name="git.branches",
    description="Read local and remote branch metadata for a registered workspace.",
    parameters={"type": "object", "properties": {"workspace_id": {"type": "string", "default": "repo"}}},
)
def ops_git_branches(workspace_id: str = "repo"):
    return get_git_ops_service().branches(workspace_id).to_dict()


@registry.register(
    name="git.remotes",
    description="Read configured Git remotes with embedded credentials redacted.",
    parameters={"type": "object", "properties": {"workspace_id": {"type": "string", "default": "repo"}}},
)
def ops_git_remotes(workspace_id: str = "repo"):
    return get_git_ops_service().remotes(workspace_id).to_dict()


@registry.register(
    name="git.activity",
    description="Read bounded Git reflog and Ananta audit activity for a registered workspace.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
        },
    },
)
def ops_git_activity(workspace_id: str = "repo", limit: int = 100):
    return get_git_ops_service().activity(workspace_id, limit=limit).to_dict()


@registry.register(
    name="git.stage",
    description="Policy-gated Git stage for explicit workspace-relative paths.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "approval_id": {"type": "string"},
        },
        "required": ["paths"],
    },
)
def ops_git_stage(paths: list[str], workspace_id: str = "repo", approval_id: str | None = None):
    return get_git_ops_service().stage(workspace_id, paths, staged=True, approval_id=approval_id).to_dict()


@registry.register(
    name="git.unstage",
    description="Policy-gated Git unstage for explicit workspace-relative paths.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "approval_id": {"type": "string"},
        },
        "required": ["paths"],
    },
)
def ops_git_unstage(paths: list[str], workspace_id: str = "repo", approval_id: str | None = None):
    return get_git_ops_service().unstage(workspace_id, paths, approval_id=approval_id).to_dict()


@registry.register(
    name="git.discard",
    description="Policy-gated discard of unstaged tracked changes; untracked files and conflicts are never deleted.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "approval_id": {"type": "string"},
        },
        "required": ["paths"],
    },
)
def ops_git_discard(paths: list[str], workspace_id: str = "repo", approval_id: str | None = None):
    return get_git_ops_service().discard(workspace_id, paths, approval_id=approval_id).to_dict()


@registry.register(
    name="git.commit",
    description="Policy-gated Git commit using the current explicit staged state.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "message": {"type": "string"},
            "approval_id": {"type": "string"},
        },
        "required": ["message"],
    },
)
def ops_git_commit(message: str, workspace_id: str = "repo", approval_id: str | None = None):
    return get_git_ops_service().commit(workspace_id, message, approval_id=approval_id).to_dict()


@registry.register(
    name="git.fetch",
    description="Policy-gated Git fetch from a configured remote; arbitrary refspecs are not accepted.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "remote": {"type": "string"},
            "approval_id": {"type": "string"},
        },
    },
)
def ops_git_fetch(workspace_id: str = "repo", remote: str | None = None, approval_id: str | None = None):
    return get_git_ops_service().fetch(workspace_id, remote=remote, approval_id=approval_id).to_dict()


@registry.register(
    name="git.pull",
    description="Policy-gated fast-forward-only pull for the configured upstream.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "remote": {"type": "string"},
            "branch": {"type": "string"},
            "approval_id": {"type": "string"},
        },
    },
)
def ops_git_pull(
    workspace_id: str = "repo",
    remote: str | None = None,
    branch: str | None = None,
    approval_id: str | None = None,
):
    return get_git_ops_service().pull(
        workspace_id,
        remote=remote,
        branch=branch,
        approval_id=approval_id,
    ).to_dict()


@registry.register(
    name="git.push",
    description="Policy-gated Git push for the configured workspace remote.",
    parameters={
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "default": "repo"},
            "remote": {"type": "string"},
            "branch": {"type": "string"},
            "approval_id": {"type": "string"},
        },
    },
)
def ops_git_push(
    workspace_id: str = "repo",
    remote: str | None = None,
    branch: str | None = None,
    approval_id: str | None = None,
):
    return get_git_ops_service().push(
        workspace_id,
        remote=remote,
        branch=branch,
        approval_id=approval_id,
    ).to_dict()


@registry.register(
    name="docker.status",
    description="Read-only Docker engine boundary and availability status.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_status():
    return get_docker_engine_service().status().to_dict()


@registry.register(
    name="docker.info",
    description="Read bounded Docker engine information through the configured hub boundary.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_info():
    return get_docker_engine_service().info()


@registry.register(
    name="docker.container_list",
    description="Read-only Docker container list through the configured hub boundary.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_container_list():
    return get_docker_engine_service().container_snapshot()


@registry.register(
    name="docker.container_logs",
    description="Read-only capped Docker container logs.",
    parameters={
        "type": "object",
        "properties": {"container_id": {"type": "string"}, "tail": {"type": "integer", "default": 200}},
        "required": ["container_id"],
    },
)
def ops_docker_container_logs(container_id: str, tail: int = 200):
    return get_docker_engine_service().logs(container_id, tail=tail)


@registry.register(
    name="docker.container_inspect",
    description="Read a credential-redacted, bounded container inspection view.",
    parameters={
        "type": "object",
        "properties": {"container_id": {"type": "string"}},
        "required": ["container_id"],
    },
)
def ops_docker_container_inspect(container_id: str):
    return get_docker_engine_service().inspect_light(container_id)


@registry.register(
    name="docker.container_stats",
    description="Read a one-shot bounded resource snapshot for a registered container.",
    parameters={
        "type": "object",
        "properties": {"container_id": {"type": "string"}},
        "required": ["container_id"],
    },
)
def ops_docker_container_stats(container_id: str):
    return get_docker_engine_service().stats(container_id)


@registry.register(
    name="docker.images",
    description="List bounded Docker image metadata.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_images():
    return get_docker_engine_service().images()


@registry.register(
    name="docker.networks",
    description="List bounded Docker network metadata.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_networks():
    return get_docker_engine_service().networks()


@registry.register(
    name="docker.volumes",
    description="List bounded Docker volume metadata without destructive controls.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_volumes():
    return get_docker_engine_service().volumes()


@registry.register(
    name="docker.disk_usage",
    description="Read bounded Docker disk-usage metadata.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_disk_usage():
    return get_docker_engine_service().disk_usage()


@registry.register(
    name="docker.container_action",
    description="Policy-gated Docker container action.",
    parameters={
        "type": "object",
        "properties": {
            "container_id": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "stop", "restart"]},
            "approval_id": {"type": "string"},
        },
        "required": ["container_id", "action"],
    },
)
def ops_docker_container_action(container_id: str, action: str, approval_id: str | None = None):
    return get_docker_engine_service().action(container_id, action, approval_id=approval_id).to_dict()


@registry.register(
    name="compose.project_list",
    description="Read-only registered Docker Compose project list.",
    parameters={"type": "object", "properties": {}},
)
def ops_compose_project_list():
    items = [item.to_dict() for item in get_docker_compose_service().projects()]
    return {"items": items, "count": len(items)}


@registry.register(
    name="compose.project_status",
    description="Read-only registered Docker Compose project status.",
    parameters={"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
)
def ops_compose_project_status(project_id: str):
    return get_docker_compose_service().status(project_id).to_dict()


@registry.register(
    name="compose.project_config",
    description="Read the normalized, bounded Docker Compose configuration for a registered project.",
    parameters={"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
)
def ops_compose_project_config(project_id: str):
    return get_docker_compose_service().config(project_id)


@registry.register(
    name="compose.project_logs",
    description="Read-only capped Docker Compose logs.",
    parameters={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "service": {"type": "string"},
            "tail": {"type": "integer", "default": 200},
        },
        "required": ["project_id"],
    },
)
def ops_compose_project_logs(project_id: str, service: str | None = None, tail: int = 200):
    return get_docker_compose_service().logs(project_id, service=service, tail=tail)


@registry.register(
    name="compose.project_action",
    description="Policy-gated Docker Compose project action.",
    parameters={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "action": {"type": "string", "enum": ["up", "stop", "restart", "pull", "down"]},
            "service": {"type": "string"},
            "approval_id": {"type": "string"},
        },
        "required": ["project_id", "action"],
    },
)
def ops_compose_project_action(
    project_id: str,
    action: str,
    service: str | None = None,
    approval_id: str | None = None,
):
    return get_docker_compose_service().action(
        project_id,
        action,
        service=service,
        approval_id=approval_id,
    ).to_dict()
