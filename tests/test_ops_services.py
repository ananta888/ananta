from __future__ import annotations

import subprocess
from pathlib import Path

from agent.services.docker_compose_service import DockerComposeService
from agent.services.docker_engine_service import DockerEngineService
from agent.services.git_ops_service import GitOpsService
from agent.services.ops_command_runner import CommandResult
from agent.services.ops_policy_service import OpsPolicyService
from agent.services.ops_registry_service import ComposeProjectRef, OpsRegistryService, WorkspaceRef


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None, *, exists: bool = True) -> None:
        self.responses = responses or {}
        self.exists_value = exists
        self.calls: list[tuple[str, ...]] = []

    def exists(self, binary: str) -> bool:
        return self.exists_value

    def run(self, args, *, cwd=None, timeout_seconds=None, env=None):
        del cwd, timeout_seconds, env
        key = tuple(args)
        self.calls.append(key)
        return self.responses.get(key, CommandResult(0, "", ""))


class AllowPolicy(OpsPolicyService):
    def evaluate(self, tool_name: str, action: str, *, target_id: str = ""):
        del tool_name, action, target_id
        from agent.services.ops_policy_service import OpsPolicyDecision

        return OpsPolicyDecision("allow", "allowed")


class ApprovalPolicy(OpsPolicyService):
    def evaluate(self, tool_name: str, action: str, *, target_id: str = ""):
        del tool_name, action, target_id
        from agent.services.ops_policy_service import OpsPolicyDecision

        return OpsPolicyDecision("approval_required", "approval_required")

    def create_approval_request(self, *, tool_name: str, action: str, target_id: str, arguments: dict):
        self.created = {"tool_name": tool_name, "action": action, "target_id": target_id, "arguments": arguments}
        return "approval-1"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.invalid"], repo)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo)
    _git(["commit", "-m", "chore(test): initial commit"], repo)
    return repo


def test_git_ops_status_and_diff_are_structured(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nchanged\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    service = GitOpsService(registry=OpsRegistryService(workspaces=[WorkspaceRef("w1", repo)]))

    status = service.status("w1")
    diff = service.diff("w1", path="tracked.txt")

    assert status.error is None
    assert status.dirty is True
    assert any(item.path == "tracked.txt" for item in status.changed_files)
    assert "changed" in diff.diff
    assert diff.error is None


def test_git_ops_rejects_unregistered_workspace_and_path_escape(tmp_path):
    repo = _make_repo(tmp_path)
    service = GitOpsService(registry=OpsRegistryService(workspaces=[WorkspaceRef("w1", repo)]))

    assert service.status("missing").error.code == "workspace_not_allowed"
    assert service.diff("w1", path="../outside.txt").error.code == "path_not_allowed"


def test_git_stage_requires_explicit_paths_and_does_not_use_add_all(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    service = GitOpsService(registry=OpsRegistryService(workspaces=[WorkspaceRef("w1", repo)]), policy=AllowPolicy())

    empty = service.stage("w1", [])
    staged = service.stage("w1", ["new.txt"])

    assert empty.error.code == "path_not_allowed"
    assert staged.ok is True
    cached = service.diff("w1", cached=True)
    assert "new.txt" in cached.diff


def test_git_mutation_approval_required_creates_approval_request(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    policy = ApprovalPolicy()
    service = GitOpsService(registry=OpsRegistryService(workspaces=[WorkspaceRef("w1", repo)]), policy=policy)

    result = service.stage("w1", ["new.txt"])

    assert result.decision == "approval_required"
    assert result.approval_id == "approval-1"
    assert policy.created["tool_name"] == "git.stage"


def test_docker_status_is_boundary_not_configured_without_config(app):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {}
        status = DockerEngineService(runner=FakeRunner()).status()

    assert status.available is False
    assert status.error.code == "docker_boundary_not_configured"


def test_docker_container_list_uses_fake_runner_without_daemon(app):
    responses = {
        ("docker", "version", "--format", "{{json .Server}}"): CommandResult(0, '{"Version":"25.0"}', ""),
        ("docker", "compose", "version", "--format", "json"): CommandResult(0, "{}", ""),
        ("docker", "ps", "--all", "--format", "{{json .}}"): CommandResult(
            0,
            '{"ID":"abcdef123456","Names":"hub","Image":"ananta:dev","Status":"Up 1 minute (healthy)","Ports":"8080->8080","Labels":"com.docker.compose.project=ananta","RunningFor":"1 minute"}\n',
            "",
        ),
    }
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        items = DockerEngineService(runner=FakeRunner(responses)).containers()

    assert items[0].id == "abcdef123456"
    assert items[0].health == "healthy"
    assert items[0].compose_project == "ananta"


def test_compose_projects_mark_preferred_and_legacy(tmp_path):
    root = tmp_path / "repo"
    next_dir = root / "docker" / "compose-next"
    old_dir = root / "docker" / "old_way"
    next_dir.mkdir(parents=True)
    old_dir.mkdir(parents=True)
    (next_dir / "compose.dev.ollama.yml").write_text("services: {}\n", encoding="utf-8")
    (old_dir / "docker-compose.e2e-local.yml").write_text("services: {}\n", encoding="utf-8")

    projects = OpsRegistryService(repo_root=root).compose_projects()

    assert {project.marker for project in projects} == {"preferred", "legacy"}
    assert any(project.category == "dev" for project in projects)
    assert any(project.category == "e2e" for project in projects)


def test_compose_status_rejects_unregistered_project():
    service = DockerComposeService(registry=OpsRegistryService(workspaces=[]), runner=FakeRunner())

    result = service.status("missing")

    assert result.error.code == "compose_project_not_registered"


def test_compose_status_parses_fake_ps(app, tmp_path):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    ref = ComposeProjectRef(
        project_id="p1",
        name="demo",
        project_directory=tmp_path,
        compose_files=(compose_file,),
        profiles=("dev",),
        marker="preferred",
        category="dev",
        allowed_actions=("status", "config", "logs"),
    )

    class Registry(OpsRegistryService):
        def compose_projects(self):
            return [ref]

        def resolve_compose_project(self, project_id: str):
            return ref if project_id == "p1" else None

    responses = {
        ("docker", "version", "--format", "{{json .Server}}"): CommandResult(0, '{"Version":"25.0"}', ""),
        ("docker", "compose", "version", "--format", "json"): CommandResult(0, "{}", ""),
        ("docker", "compose", "-f", str(compose_file), "ps", "--format", "json"): CommandResult(
            0,
            '{"Service":"hub","State":"running","Health":"healthy","ExitCode":0}\n',
            "",
        ),
    }
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        docker = DockerEngineService(runner=FakeRunner(responses))
        service = DockerComposeService(registry=Registry(), runner=FakeRunner(responses), docker=docker)
        result = service.status("p1")

    assert result.error is None
    assert result.services[0].name == "hub"
    assert result.services[0].health == "healthy"
