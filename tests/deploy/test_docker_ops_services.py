from __future__ import annotations

from pathlib import Path

from agent.services.docker_compose_service import DockerComposeService
from agent.services.docker_engine_service import DockerEngineService
from agent.services.ops_command_runner import CommandResult
from agent.services.ops_models import DockerEngineStatus
from agent.services.ops_policy_service import OpsPolicyDecision
from agent.services.ops_registry_service import ComposeProjectRef, OpsRegistryService


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None, *, exists: bool = True) -> None:
        self.responses = responses or {}
        self.exists_value = exists
        self.calls: list[dict] = []

    def exists(self, binary: str) -> bool:
        return self.exists_value

    def run(self, args, *, cwd=None, timeout_seconds=None, env=None):
        call = {"args": tuple(args), "cwd": cwd, "timeout": timeout_seconds, "env": env}
        self.calls.append(call)
        return self.responses.get(tuple(args), CommandResult(0, "", ""))


class AllowPolicy:
    def __init__(self) -> None:
        self.authorized: list[dict] = []
        self.consumed: list[str | None] = []

    def authorize(self, tool_name, action, *, target_id="", arguments=None, approval_id=None):
        self.authorized.append(
            {
                "tool_name": tool_name,
                "action": action,
                "target_id": target_id,
                "arguments": arguments,
                "approval_id": approval_id,
            }
        )
        return OpsPolicyDecision("allow", "allowed", {"approval_id": approval_id})

    def consume_approval(self, approval_id):
        self.consumed.append(approval_id)


class ApprovalPolicy:
    def __init__(self) -> None:
        self.created: dict | None = None

    def authorize(self, tool_name, action, *, target_id="", arguments=None, approval_id=None):
        del tool_name, action, target_id, arguments, approval_id
        return OpsPolicyDecision("approval_required", "approval_required")

    def create_approval_request(self, **kwargs):
        self.created = kwargs
        return "approval-1"

    def consume_approval(self, approval_id):
        raise AssertionError(f"blocked action consumed {approval_id}")


class RejectedApprovalPolicy:
    def authorize(self, tool_name, action, *, target_id="", arguments=None, approval_id=None):
        del tool_name, action, target_id, arguments
        return OpsPolicyDecision(
            "policy_denied",
            "approval_digest_mismatch",
            {"approval_id": approval_id},
        )

    def create_approval_request(self, **kwargs):
        raise AssertionError(f"denied approval created a new request: {kwargs}")

    def consume_approval(self, approval_id):
        raise AssertionError(f"denied approval was consumed: {approval_id}")


class AvailableDocker:
    @staticmethod
    def status():
        return DockerEngineStatus(True, boundary="hub_cli", docker_version="25.0", compose_available=True)


def _docker_responses(container_line: str = "") -> dict[tuple[str, ...], CommandResult]:
    return {
        ("docker", "version", "--format", "{{json .Server}}"): CommandResult(
            0,
            '{"Version":"25.0","ApiVersion":"1.44","Os":"linux","Arch":"amd64"}',
            "",
        ),
        ("docker", "compose", "version", "--format", "json"): CommandResult(0, '{"version":"2.24"}', ""),
        ("docker", "ps", "--all", "--size", "--format", "{{json .}}"): CommandResult(0, container_line, ""),
    }


def _container_line() -> str:
    return (
        '{"ID":"abcdef123456","Names":"hub","Image":"ananta:dev",'
        '"Status":"Up 1 minute (healthy)","State":"running","Ports":"8080->8080",'
        '"Labels":"com.docker.compose.project=ananta","RunningFor":"1 minute",'
        '"Command":"python app.py","CreatedAt":"today","Size":"1MB",'
        '"Networks":"ananta_default","Mounts":"ananta_data"}\n'
    )


def _managed_config(app) -> None:
    app.config["AGENT_CONFIG"] = {
        "docker_ops": {
            "boundary": "hub_cli",
            "managed_containers": [
                {
                    "compose_project": "ananta",
                    "allowed_actions": ["logs", "inspect_light", "stats", "start", "stop", "restart"],
                }
            ],
        }
    }


def test_docker_status_rejects_unknown_boundary(app):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "socket_magic"}}
        status = DockerEngineService(runner=FakeRunner()).status()

    assert status.available is False
    assert status.error.code == "docker_boundary_not_configured"


def test_docker_info_is_structured_and_omits_sensitive_host_paths(app):
    responses = _docker_responses()
    responses[("docker", "info", "--format", "{{json .}}")] = CommandResult(
        0,
        '{"ID":"engine-1","Name":"dev","ServerVersion":"25.0","OperatingSystem":"Linux",'
        '"Architecture":"x86_64","NCPU":8,"MemTotal":16000000,"Containers":4,'
        '"ContainersRunning":3,"Images":12,"DockerRootDir":"/sensitive/host/path",'
        '"SecurityOptions":["name=seccomp"],"Warnings":["warning"]}',
        "",
    )
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        result = DockerEngineService(runner=FakeRunner(responses)).info()

    assert result["ok"] is True
    assert result["info"]["containers_running"] == 3
    assert result["info"]["cpus"] == 8
    assert "docker_root_dir" not in result["info"]
    assert "/sensitive/host/path" not in str(result)


def test_docker_status_short_cache_avoids_repeated_cli_probes(app):
    runner = FakeRunner(_docker_responses())
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        service = DockerEngineService(runner=runner)
        assert service.status().available is True
        assert service.status().available is True

    version_calls = [
        call for call in runner.calls if call["args"] == ("docker", "version", "--format", "{{json .Server}}")
    ]
    assert len(version_calls) == 1


def test_container_snapshot_marks_managed_targets_and_exactly_resolves_ids(app):
    runner = FakeRunner(_docker_responses(_container_line()))
    with app.app_context():
        _managed_config(app)
        service = DockerEngineService(runner=runner, registry=OpsRegistryService())
        items = service.containers()
        rejected = service.inspect_light("abcdef")

    assert items[0].id == "abcdef123456"
    assert items[0].state == "running"
    assert items[0].managed is True
    assert "restart" in items[0].allowed_actions
    assert items[0].networks == ["ananta_default"]
    assert rejected["error"]["code"] == "docker_container_not_registered"
    assert not any(call["args"][:2] == ("docker", "inspect") for call in runner.calls)


def test_container_snapshot_surfaces_daemon_list_failure(app):
    responses = _docker_responses()
    responses[("docker", "ps", "--all", "--size", "--format", "{{json .}}")] = CommandResult(
        1, "", "daemon refused container listing"
    )
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        snapshot = DockerEngineService(runner=FakeRunner(responses)).container_snapshot()

    assert snapshot["ok"] is False
    assert snapshot["items"] == []
    assert snapshot["error"]["code"] == "docker_unreachable"


def test_container_inspect_and_stats_are_sanitized_and_bounded(app):
    responses = _docker_responses(_container_line())
    responses[("docker", "inspect", "abcdef123456")] = CommandResult(
        0,
        '[{"Id":"abcdef1234567890","Name":"/hub","Created":"today","Platform":"linux",'
        '"Config":{"Image":"ananta:dev","Env":["SECRET=never-return"],"Labels":{"role":"hub"}},'
        '"State":{"Status":"running","Running":true,"Pid":42,"Health":{"Status":"healthy",'
        '"FailingStreak":0,"Log":[{"Output":"secret output"}]}},'
        '"HostConfig":{"Memory":1024,"NanoCpus":2000000000,"Privileged":false,'
        '"RestartPolicy":{"Name":"unless-stopped"}},'
        '"Mounts":[{"Type":"bind","Source":"/secret/source","Destination":"/workspace","RW":true}],'
        '"NetworkSettings":{"Networks":{"default":{"IPAddress":"172.1.0.2"}},"Ports":{}}}]',
        "",
    )
    responses[("docker", "stats", "--no-stream", "--format", "{{json .}}", "abcdef123456")] = CommandResult(
        0,
        '{"CPUPerc":"1.2%","MemUsage":"12MiB / 1GiB","MemPerc":"1.1%","NetIO":"1kB / 2kB",'
        '"BlockIO":"3kB / 4kB","PIDs":"7"}\n',
        "",
    )
    with app.app_context():
        _managed_config(app)
        service = DockerEngineService(runner=FakeRunner(responses), registry=OpsRegistryService())
        inspect = service.inspect_light("abcdef123456")
        stats = service.stats("hub")

    assert inspect["inspect"]["resources"]["memory_bytes"] == 1024
    assert inspect["inspect"]["mounts"][0]["destination"] == "/workspace"
    assert "/secret/source" not in str(inspect)
    assert "SECRET=never-return" not in str(inspect)
    assert "secret output" not in str(inspect)
    assert stats["stats"]["cpu_percent"] == "1.2%"
    assert stats["stats"]["pids"] == "7"


def test_container_action_requires_management_registration(app):
    runner = FakeRunner(_docker_responses(_container_line()))
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        result = DockerEngineService(
            runner=runner,
            registry=OpsRegistryService(),
            policy=AllowPolicy(),
        ).action("abcdef123456", "restart")

    assert result.ok is False
    assert result.error.code == "policy_denied"
    assert ("docker", "restart", "abcdef123456") not in [call["args"] for call in runner.calls]


def test_container_action_is_digest_bound_audited_and_consumes_grant(app):
    responses = _docker_responses(_container_line())
    responses[("docker", "restart", "abcdef123456")] = CommandResult(0, "abcdef123456\n", "")
    runner = FakeRunner(responses)
    policy = AllowPolicy()
    with app.app_context():
        _managed_config(app)
        result = DockerEngineService(
            runner=runner,
            registry=OpsRegistryService(),
            policy=policy,
        ).action("hub", "restart", approval_id="grant-1")

    assert result.ok is True
    assert policy.authorized == [
        {
            "tool_name": "docker.container_action",
            "action": "restart",
            "target_id": "abcdef123456",
            "arguments": {"container_id": "abcdef123456", "action": "restart"},
            "approval_id": "grant-1",
        }
    ]
    assert policy.consumed == ["grant-1"]


def test_container_action_creates_approval_for_exact_resolved_target(app):
    policy = ApprovalPolicy()
    with app.app_context():
        _managed_config(app)
        result = DockerEngineService(
            runner=FakeRunner(_docker_responses(_container_line())),
            registry=OpsRegistryService(),
            policy=policy,
        ).action("hub", "stop")

    assert result.decision == "approval_required"
    assert result.approval_id == "approval-1"
    assert policy.created == {
        "tool_name": "docker.container_action",
        "action": "stop",
        "target_id": "abcdef123456",
        "arguments": {"container_id": "abcdef123456", "action": "stop"},
    }


def test_docker_resource_lists_return_structured_bounded_items(app):
    responses = _docker_responses()
    responses[("docker", "image", "ls", "--all", "--digests", "--format", "{{json .}}")] = CommandResult(
        0, '{"ID":"sha256:1","Repository":"ananta","Tag":"dev","Size":"1GB"}\n', ""
    )
    responses[("docker", "network", "ls", "--no-trunc", "--format", "{{json .}}")] = CommandResult(
        0, '{"ID":"net-1","Name":"ananta_default","Driver":"bridge","IPv6":"false"}\n', ""
    )
    responses[("docker", "volume", "ls", "--format", "{{json .}}")] = CommandResult(
        0, '{"Name":"ananta_data","Driver":"local"}\n', ""
    )
    responses[("docker", "system", "df", "--format", "{{json .}}")] = CommandResult(
        0, '{"Type":"Images","TotalCount":"2","Size":"1GB","Reclaimable":"10MB"}\n', ""
    )
    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {"boundary": "hub_cli"}}
        service = DockerEngineService(runner=FakeRunner(responses))
        images = service.images()
        networks = service.networks()
        volumes = service.volumes()
        disk = service.disk_usage()

    assert images["items"][0]["id"] == "sha256:1"
    assert networks["items"][0]["ipv6"] == "false"
    assert volumes["items"][0]["name"] == "ananta_data"
    assert disk["items"][0]["total_count"] == "2"


def test_registry_supports_explicit_multifile_profiles_and_rejects_escape(app, tmp_path):
    root = tmp_path / "repo"
    compose_dir = root / "docker" / "compose-next"
    compose_dir.mkdir(parents=True)
    (root / ".env.ops").write_text("SECRET=test-only\n", encoding="utf-8")
    (compose_dir / "compose.stack.full.yml").write_text("services: {}\n", encoding="utf-8")
    (compose_dir / "compose.voice.yml").write_text("services: {}\n", encoding="utf-8")
    outside = tmp_path / "outside.yml"
    outside.write_text("services: {}\n", encoding="utf-8")
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "docker_ops": {
                "compose_projects": [
                    {
                        "id": "voice-prod",
                        "name": "Voice production",
                        "directory": "docker/compose-next",
                        "files": [
                            "docker/compose-next/compose.stack.full.yml",
                            "docker/compose-next/compose.voice.yml",
                        ],
                        "env_files": [".env.ops"],
                        "profiles": ["voice-production-cpu"],
                        "available_profiles": ["voice-production-cpu", "voice-production-nvidia"],
                        "project_name": "ananta-voice",
                        "allowed_actions": ["status", "config", "logs", "pull", "up", "down"],
                    },
                    {
                        "id": "escaped",
                        "directory": "docker/compose-next",
                        "files": [str(outside)],
                    },
                ]
            }
        }
        projects = OpsRegistryService(repo_root=root).compose_projects()

    assert len(projects) == 1
    assert projects[0].project_id == "voice-prod"
    assert len(projects[0].compose_files) == 2
    assert projects[0].profiles == ("voice-production-cpu",)
    assert projects[0].env_files == ((root / ".env.ops").resolve(),)


def test_discovered_compose_uses_server_selected_read_only_env_file(app, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    compose_dir = root / "docker" / "compose-next"
    compose_dir.mkdir(parents=True)
    (compose_dir / "compose.stack.quickstart.yml").write_text("services: {}\n", encoding="utf-8")
    selected_env = tmp_path / "runtime.env"
    selected_env.write_text("POSTGRES_PASSWORD=test-only\n", encoding="utf-8")
    monkeypatch.setenv("ANANTA_DOCKER_OPS_ENV_FILE", str(selected_env))

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {}}
        projects = OpsRegistryService(repo_root=root).compose_projects()

    assert len(projects) == 1
    assert projects[0].env_files == (selected_env.resolve(),)


def test_discovered_ci_compose_is_read_only_while_dev_is_policy_gated_mutable(app, tmp_path):
    root = tmp_path / "repo"
    compose_dir = root / "docker" / "compose-next"
    compose_dir.mkdir(parents=True)
    (compose_dir / "compose.dev.lmstudio.yml").write_text("services: {}\n", encoding="utf-8")
    (compose_dir / "compose.tests.lmstudio.yml").write_text("services: {}\n", encoding="utf-8")

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"docker_ops": {}}
        projects = OpsRegistryService(repo_root=root).compose_projects()

    dev = next(project for project in projects if project.category == "dev")
    tests = next(project for project in projects if project.category == "ci")
    assert "up" in dev.allowed_actions
    assert tests.allowed_actions == ("status", "config", "logs")


def _compose_ref(tmp_path: Path, *, allowed_actions: tuple[str, ...] | None = None) -> ComposeProjectRef:
    first = tmp_path / "compose.base.yml"
    second = tmp_path / "compose.feature.yml"
    env_file = tmp_path / ".env"
    for path in (first, second):
        path.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("TOKEN=test-only\n", encoding="utf-8")
    return ComposeProjectRef(
        project_id="p1",
        name="demo",
        project_directory=tmp_path,
        compose_files=(first, second),
        profiles=("dev",),
        marker="preferred",
        category="dev",
        allowed_actions=allowed_actions or ("status", "config", "logs", "pull", "up", "stop", "restart", "down"),
        project_name="ananta-demo",
        available_profiles=("dev", "debug"),
        env_files=(env_file,),
    )


def _registry_for(ref: ComposeProjectRef) -> OpsRegistryService:
    return OpsRegistryService(repo_root=ref.project_directory, compose_projects=[ref])


def _compose_prefix(ref: ComposeProjectRef) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        "ananta-demo",
        "--env-file",
        str(ref.env_files[0]),
        "-f",
        str(ref.compose_files[0]),
        "-f",
        str(ref.compose_files[1]),
        "--profile",
        "dev",
    )


def test_compose_status_uses_registered_multifile_project_name_profiles_and_services(tmp_path):
    ref = _compose_ref(tmp_path)
    prefix = _compose_prefix(ref)
    responses = {
        (*prefix, "ps", "--all", "--format", "json"): CommandResult(
            0,
            '[{"ID":"abcdef123456","Service":"hub","State":"running","Health":"healthy",'
            '"ExitCode":0,"Image":"ananta:dev","Publishers":[{"PublishedPort":5000,'
            '"TargetPort":5000,"Protocol":"tcp"}]}]',
            "",
        ),
        (*prefix, "config", "--profiles"): CommandResult(0, "dev\ndebug\n", ""),
    }
    runner = FakeRunner(responses)
    service = DockerComposeService(
        registry=_registry_for(ref), runner=runner, docker=AvailableDocker(), policy=AllowPolicy()
    )

    result = service.status("p1")

    assert result.error is None
    assert result.available_profiles == ["dev", "debug"]
    assert result.services[0].container_id == "abcdef123456"
    assert result.services[0].ports == "5000->5000/tcp"
    assert runner.calls[0]["cwd"] == tmp_path


def test_compose_config_does_not_resolve_env_files_or_host_paths(tmp_path):
    ref = _compose_ref(tmp_path)
    prefix = _compose_prefix(ref)
    safe_config_args = (
        *prefix,
        "config",
        "--no-interpolate",
        "--no-env-resolution",
        "--no-path-resolution",
    )
    runner = FakeRunner(
        {
            safe_config_args: CommandResult(0, "services:\n  hub:\n    image: ${HUB_IMAGE}\n", ""),
            (*prefix, "config", "--services"): CommandResult(0, "hub\n", ""),
            (*prefix, "config", "--profiles"): CommandResult(0, "dev\n", ""),
        }
    )
    service = DockerComposeService(registry=_registry_for(ref), runner=runner, docker=AvailableDocker())

    result = service.config("p1")

    assert result["ok"] is True
    assert "${HUB_IMAGE}" in result["config"]
    assert safe_config_args in [call["args"] for call in runner.calls]
    assert "TOKEN=test-only" not in result["config"]


def test_compose_logs_validate_service_against_registered_config(tmp_path):
    ref = _compose_ref(tmp_path)
    prefix = _compose_prefix(ref)
    runner = FakeRunner({(*prefix, "config", "--services"): CommandResult(0, "hub\nworker\n", "")})
    service = DockerComposeService(registry=_registry_for(ref), runner=runner, docker=AvailableDocker())

    result = service.logs("p1", service="unknown")

    assert result["ok"] is False
    assert result["error"]["code"] == "compose_file_invalid"
    assert not any("logs" in call["args"] for call in runner.calls)


def test_compose_allowed_actions_are_enforced_before_policy_and_cli(tmp_path):
    ref = _compose_ref(tmp_path, allowed_actions=("status", "config", "logs"))
    runner = FakeRunner()
    policy = AllowPolicy()
    service = DockerComposeService(registry=_registry_for(ref), runner=runner, docker=AvailableDocker(), policy=policy)

    result = service.action("p1", "down")

    assert result.error.code == "policy_denied"
    assert policy.authorized == []
    assert runner.calls == []


def test_compose_down_never_adds_volume_deletion_and_consumes_approval(tmp_path):
    ref = _compose_ref(tmp_path)
    prefix = _compose_prefix(ref)
    runner = FakeRunner({(*prefix, "down"): CommandResult(0, "stopped\n", "")})
    policy = AllowPolicy()
    service = DockerComposeService(registry=_registry_for(ref), runner=runner, docker=AvailableDocker(), policy=policy)

    result = service.action("p1", "down", approval_id="grant-2")

    assert result.ok is True
    assert runner.calls[-1]["args"] == (*prefix, "down")
    assert "--volumes" not in runner.calls[-1]["args"]
    assert "-v" not in runner.calls[-1]["args"]
    assert policy.authorized[0]["arguments"] == {"project_id": "p1", "action": "down"}
    assert policy.consumed == ["grant-2"]


def test_compose_service_action_validates_and_binds_registered_service(tmp_path):
    ref = _compose_ref(tmp_path)
    prefix = _compose_prefix(ref)
    runner = FakeRunner(
        {
            (*prefix, "config", "--services"): CommandResult(0, "hub\nworker\n", ""),
            (*prefix, "restart", "worker"): CommandResult(0, "worker\n", ""),
        }
    )
    policy = AllowPolicy()
    service = DockerComposeService(registry=_registry_for(ref), runner=runner, docker=AvailableDocker(), policy=policy)

    result = service.action("p1", "restart", service="worker", approval_id="grant-service")

    assert result.ok is True
    assert runner.calls[-1]["args"] == (*prefix, "restart", "worker")
    assert policy.authorized[0]["arguments"] == {
        "project_id": "p1",
        "action": "restart",
        "service": "worker",
    }


def test_compose_approval_request_uses_exact_registered_project(tmp_path):
    ref = _compose_ref(tmp_path)
    policy = ApprovalPolicy()
    service = DockerComposeService(
        registry=_registry_for(ref), runner=FakeRunner(), docker=AvailableDocker(), policy=policy
    )

    result = service.action("p1", "pull")

    assert result.approval_id == "approval-1"
    assert policy.created == {
        "tool_name": "compose.project_action",
        "action": "pull",
        "target_id": "p1",
        "arguments": {"project_id": "p1", "action": "pull"},
    }


def test_compose_rejected_approval_id_is_not_returned_as_retryable(tmp_path):
    ref = _compose_ref(tmp_path)
    service = DockerComposeService(
        registry=_registry_for(ref),
        runner=FakeRunner(),
        docker=AvailableDocker(),
        policy=RejectedApprovalPolicy(),
    )

    result = service.action("p1", "pull", approval_id="wrong-grant")

    assert result.ok is False
    assert result.decision == "policy_denied"
    assert result.approval_id is None
    assert result.error.code == "policy_denied"
    assert result.error.message == "approval_digest_mismatch"
