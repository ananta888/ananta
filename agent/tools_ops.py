from __future__ import annotations

from agent.services.docker_compose_service import get_docker_compose_service
from agent.services.docker_engine_service import get_docker_engine_service
from agent.services.git_ops_service import get_git_ops_service
from agent.tools_registry import registry


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
        },
    },
)
def ops_git_diff(workspace_id: str = "repo", path: str | None = None, cached: bool = False):
    return get_git_ops_service().diff(workspace_id, path=path, cached=cached).to_dict()


@registry.register(
    name="git.stage",
    description="Policy-gated Git stage for explicit workspace-relative paths.",
    parameters={
        "type": "object",
        "properties": {"workspace_id": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}},
        "required": ["paths"],
    },
)
def ops_git_stage(paths: list[str], workspace_id: str = "repo"):
    return get_git_ops_service().stage(workspace_id, paths, staged=True).to_dict()


@registry.register(
    name="git.commit",
    description="Policy-gated Git commit using the current explicit staged state.",
    parameters={
        "type": "object",
        "properties": {"workspace_id": {"type": "string"}, "message": {"type": "string"}},
        "required": ["message"],
    },
)
def ops_git_commit(message: str, workspace_id: str = "repo"):
    return get_git_ops_service().commit(workspace_id, message).to_dict()


@registry.register(
    name="git.push",
    description="Policy-gated Git push for the configured workspace remote.",
    parameters={"type": "object", "properties": {"workspace_id": {"type": "string", "default": "repo"}}},
)
def ops_git_push(workspace_id: str = "repo"):
    return get_git_ops_service().push(workspace_id).to_dict()


@registry.register(
    name="docker.status",
    description="Read-only Docker engine boundary and availability status.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_status():
    return get_docker_engine_service().status().to_dict()


@registry.register(
    name="docker.container_list",
    description="Read-only Docker container list through the configured hub boundary.",
    parameters={"type": "object", "properties": {}},
)
def ops_docker_container_list():
    items = [item.to_dict() for item in get_docker_engine_service().containers()]
    return {"items": items, "count": len(items)}


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
    name="docker.container_action",
    description="Policy-gated Docker container action.",
    parameters={
        "type": "object",
        "properties": {"container_id": {"type": "string"}, "action": {"type": "string"}},
        "required": ["container_id", "action"],
    },
)
def ops_docker_container_action(container_id: str, action: str):
    return get_docker_engine_service().action(container_id, action).to_dict()


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
    name="compose.project_logs",
    description="Read-only capped Docker Compose logs.",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "string"}, "service": {"type": "string"}, "tail": {"type": "integer", "default": 200}},
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
        "properties": {"project_id": {"type": "string"}, "action": {"type": "string"}},
        "required": ["project_id", "action"],
    },
)
def ops_compose_project_action(project_id: str, action: str):
    return get_docker_compose_service().action(project_id, action).to_dict()
