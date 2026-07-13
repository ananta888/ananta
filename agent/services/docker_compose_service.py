from __future__ import annotations

import json
from typing import Any

from agent.common.audit import log_audit
from agent.services.docker_engine_service import DockerEngineService, get_docker_engine_service
from agent.services.ops_command_runner import CommandResult, CommandRunner, get_default_command_runner
from agent.services.ops_models import ComposeProjectSummary, ComposeServiceStatus, OpsActionResult, OpsError
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import ComposeProjectRef, OpsRegistryService, get_ops_registry_service

_COMPOSE_ACTIONS = frozenset({"pull", "up", "stop", "restart", "down"})


class DockerComposeService:
    """Docker Compose control surface over registered project definitions.

    A project ID resolves to a server-owned set of compose files, profiles,
    env-files and a stable Compose project name. Clients can never submit host
    paths or arbitrary profile names. Destructive volume deletion is not part
    of this interface.
    """

    def __init__(
        self,
        *,
        registry: OpsRegistryService | None = None,
        runner: CommandRunner | None = None,
        docker: DockerEngineService | None = None,
        policy: OpsPolicyService | None = None,
    ) -> None:
        self._registry = registry or get_ops_registry_service()
        self._runner = runner or get_default_command_runner()
        self._docker = docker or get_docker_engine_service()
        self._policy = policy or get_ops_policy_service()

    def projects(self) -> list[ComposeProjectSummary]:
        return [self._summary_from_ref(ref) for ref in self._registry.compose_projects()]

    def status(self, project_id: str) -> ComposeProjectSummary:
        ref, error = self._resolve_project(project_id, "status")
        if error:
            return self._error_summary(project_id, error)
        summary = self._summary_from_ref(ref)
        docker_status = self._docker.status()
        if not docker_status.available:
            return ComposeProjectSummary(**{**summary.to_dict(), "error": docker_status.error})
        ps = self._compose(ref, ["ps", "--all", "--format", "json"], timeout_seconds=15)
        if ps.returncode != 0:
            return ComposeProjectSummary(**{**summary.to_dict(), "error": self._compose_error(ps)})
        profiles = self._available_profiles(ref)
        return ComposeProjectSummary(
            **{
                **summary.to_dict(),
                "services": self._parse_ps(ps.stdout),
                "available_profiles": list(profiles),
            }
        )

    def config(self, project_id: str) -> dict[str, Any]:
        ref, error = self._resolve_project(project_id, "config")
        if error:
            return {"ok": False, "error": error.to_dict()}
        unavailable = self._docker_unavailable()
        if unavailable:
            return unavailable
        result = self._compose(
            ref,
            ["config", "--no-interpolate", "--no-env-resolution", "--no-path-resolution"],
            timeout_seconds=20,
        )
        if result.returncode != 0:
            return self._failed_result(result)
        services = self._compose(ref, ["config", "--services"], timeout_seconds=20)
        profiles = self._compose(ref, ["config", "--profiles"], timeout_seconds=20)
        return {
            "ok": True,
            "project_id": ref.project_id,
            "project_name": ref.project_name,
            "config": result.stdout,
            "services": self._lines(services.stdout) if services.returncode == 0 else [],
            "available_profiles": (
                self._lines(profiles.stdout) if profiles.returncode == 0 else list(ref.available_profiles)
            ),
            "stderr": result.stderr[:500],
            "truncated": result.truncated,
        }

    def logs(
        self,
        project_id: str,
        *,
        service: str | None = None,
        tail: int = 200,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        ref, error = self._resolve_project(project_id, "logs")
        if error:
            return {"ok": False, "error": error.to_dict()}
        unavailable = self._docker_unavailable()
        if unavailable:
            return unavailable
        selected_service = str(service or "").strip()
        if selected_service:
            valid_services, service_error = self._service_names(ref)
            if service_error:
                return {"ok": False, "error": service_error.to_dict()}
            if selected_service not in valid_services:
                return {
                    "ok": False,
                    "error": OpsError(
                        "compose_file_invalid",
                        "compose service is not registered",
                        {"service": selected_service},
                    ).to_dict(),
                }
        safe_tail = self._bounded_tail(tail)
        args = ["logs", "--no-color", "--tail", str(safe_tail)]
        if timestamps:
            args.append("--timestamps")
        if selected_service:
            args.append(selected_service)
        result = self._compose(ref, args, timeout_seconds=15)
        if result.returncode != 0:
            return self._failed_result(result)
        return {
            "ok": True,
            "project_id": ref.project_id,
            "service": selected_service,
            "logs": result.stdout,
            "stderr": result.stderr,
            "tail": safe_tail,
            "truncated": result.truncated,
        }

    def action(
        self,
        project_id: str,
        action: str,
        *,
        service: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        action = str(action or "").strip().lower()
        ref, error = self._resolve_project(project_id, action)
        if error:
            decision = "policy_denied" if error.code == "policy_denied" else "allow"
            return OpsActionResult(False, action, target_id=project_id, decision=decision, error=error)
        if action not in _COMPOSE_ACTIONS:
            return OpsActionResult(
                False,
                action,
                target_id=project_id,
                decision="policy_denied",
                error=OpsError("policy_denied", "unsupported compose action"),
            )
        docker_status = self._docker.status()
        if not docker_status.available:
            return OpsActionResult(False, action, target_id=project_id, error=docker_status.error)
        selected_service = str(service or "").strip()
        if selected_service:
            if action == "down":
                return OpsActionResult(
                    False,
                    action,
                    target_id=ref.project_id,
                    decision="policy_denied",
                    error=OpsError("policy_denied", "compose down cannot target a single service"),
                )
            valid_services, service_error = self._service_names(ref)
            if service_error:
                return OpsActionResult(False, action, target_id=ref.project_id, error=service_error)
            if selected_service not in valid_services:
                return OpsActionResult(
                    False,
                    action,
                    target_id=ref.project_id,
                    decision="policy_denied",
                    error=OpsError(
                        "policy_denied",
                        "compose service is not registered",
                        {"service": selected_service},
                    ),
                )
        arguments = {"project_id": ref.project_id, "action": action}
        if selected_service:
            arguments["service"] = selected_service
        decision = self._policy.authorize(
            "compose.project_action",
            action,
            target_id=ref.project_id,
            arguments=arguments,
            approval_id=approval_id,
        )
        if not decision.allowed:
            code = "approval_required" if decision.decision == "approval_required" else "policy_denied"
            attempted_approval_id = str(decision.metadata.get("approval_id") or approval_id or "") or None
            response_approval_id: str | None = None
            if decision.decision == "approval_required":
                response_approval_id = attempted_approval_id or self._policy.create_approval_request(
                    tool_name="compose.project_action", action=action, target_id=ref.project_id, arguments=arguments
                )
            log_audit(
                "ops_compose_action_blocked",
                {
                    "action": action,
                    "project_id": ref.project_id,
                    "decision": decision.decision,
                    "approval_id": attempted_approval_id or response_approval_id,
                },
            )
            return OpsActionResult(
                False,
                action,
                target_id=ref.project_id,
                decision=decision.decision,
                approval_id=response_approval_id,
                error=OpsError(code, decision.reason_code),
            )
        args = self._action_args(action, service=selected_service)
        result = self._compose(ref, args, timeout_seconds=self._action_timeout(action))
        ok = result.returncode == 0
        if ok:
            self._policy.consume_approval(approval_id)
        log_audit(
            "ops_compose_action",
            {
                "action": action,
                "project_id": ref.project_id,
                "project_name": ref.project_name,
                "service": selected_service,
                "ok": ok,
            },
        )
        return OpsActionResult(
            ok,
            action,
            target_id=ref.project_id,
            error=None if ok else self._compose_error(result),
            approval_id=approval_id,
            metadata={
                "project_name": ref.project_name,
                "service": selected_service,
                "output": result.stdout.strip()[:500],
            },
        )

    def _resolve_project(self, project_id: str, action: str) -> tuple[ComposeProjectRef | None, OpsError | None]:
        ref = self._registry.resolve_compose_project(project_id)
        if ref is None:
            return None, OpsError("compose_project_not_registered", "compose project is not registered")
        if action not in set(ref.allowed_actions):
            log_audit(
                "ops_compose_action_blocked",
                {
                    "action": action,
                    "project_id": ref.project_id,
                    "decision": "policy_denied",
                    "reason": "action_not_registered",
                },
            )
            return None, OpsError("policy_denied", "action is not registered for this compose project")
        return ref, None

    def _summary_from_ref(self, ref: ComposeProjectRef) -> ComposeProjectSummary:
        return ComposeProjectSummary(
            project_id=ref.project_id,
            name=ref.name,
            project_directory=str(ref.project_directory),
            compose_files=[str(path) for path in ref.compose_files],
            profiles=list(ref.profiles),
            available_profiles=list(ref.available_profiles),
            marker=ref.marker,
            category=ref.category,
            allowed_actions=list(ref.allowed_actions),
        )

    @staticmethod
    def _error_summary(project_id: str, error: OpsError) -> ComposeProjectSummary:
        return ComposeProjectSummary(
            project_id=str(project_id or ""),
            name="",
            project_directory="",
            compose_files=[],
            error=error,
        )

    def _docker_unavailable(self) -> dict[str, Any] | None:
        status = self._docker.status()
        if status.available:
            return None
        return {"ok": False, "error": status.error.to_dict() if status.error else None}

    def _service_names(self, ref: ComposeProjectRef) -> tuple[set[str], OpsError | None]:
        result = self._compose(ref, ["config", "--services"], timeout_seconds=20)
        if result.returncode != 0:
            return set(), self._compose_error(result)
        return set(self._lines(result.stdout)), None

    def _available_profiles(self, ref: ComposeProjectRef) -> tuple[str, ...]:
        result = self._compose(ref, ["config", "--profiles"], timeout_seconds=20)
        if result.returncode != 0:
            return ref.available_profiles
        return tuple(self._lines(result.stdout))

    def _compose(self, ref: ComposeProjectRef, args: list[str], *, timeout_seconds: int) -> CommandResult:
        cmd = ["docker", "compose"]
        if ref.project_name:
            cmd.extend(["--project-name", ref.project_name])
        for env_file in ref.env_files:
            cmd.extend(["--env-file", str(env_file)])
        for compose_file in ref.compose_files:
            cmd.extend(["-f", str(compose_file)])
        for profile in ref.profiles:
            cmd.extend(["--profile", profile])
        cmd.extend(args)
        return self._runner.run(cmd, cwd=ref.project_directory, timeout_seconds=timeout_seconds)

    @staticmethod
    def _action_args(action: str, *, service: str = "") -> list[str]:
        if action == "up":
            args = ["up", "-d"]
        else:
            # In particular, ``down`` never includes --volumes/-v. Volume
            # removal remains a dangerous, centrally denied operation.
            args = [action]
        if service:
            args.append(service)
        return args

    @staticmethod
    def _action_timeout(action: str) -> int:
        if action == "pull":
            return 300
        if action == "up":
            return 180
        return 90

    @staticmethod
    def _parse_ps(stdout: str) -> list[ComposeServiceStatus]:
        services: list[ComposeServiceStatus] = []
        raw = stdout.strip()
        if not raw:
            return services
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
        except (TypeError, ValueError):
            items = []
            for line in raw.splitlines():
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict):
                    items.append(item)
        for item in items:
            if not isinstance(item, dict):
                continue
            services.append(
                ComposeServiceStatus(
                    name=str(item.get("Service") or item.get("Name") or ""),
                    state=str(item.get("State") or item.get("Status") or ""),
                    health=str(item.get("Health") or ""),
                    exit_code=str(item.get("ExitCode") if item.get("ExitCode") is not None else ""),
                    ports=DockerComposeService._publishers(item.get("Publishers") or item.get("Ports")),
                    container_id=str(item.get("ID") or "")[:12],
                    image=str(item.get("Image") or ""),
                    command=str(item.get("Command") or ""),
                    created_at=str(item.get("CreatedAt") or ""),
                )
            )
        return services

    @staticmethod
    def _publishers(value: Any) -> str:
        if isinstance(value, str):
            return value
        rendered: list[str] = []
        for publisher in list(value or []):
            if not isinstance(publisher, dict):
                continue
            target = publisher.get("TargetPort")
            published = publisher.get("PublishedPort")
            protocol = str(publisher.get("Protocol") or "tcp")
            url = str(publisher.get("URL") or "")
            if published:
                rendered.append(f"{url + ':' if url else ''}{published}->{target}/{protocol}")
            elif target:
                rendered.append(f"{target}/{protocol}")
        return ", ".join(rendered)

    @staticmethod
    def _lines(value: str) -> list[str]:
        return [line.strip() for line in str(value or "").splitlines() if line.strip()]

    @staticmethod
    def _bounded_tail(value: Any) -> int:
        try:
            parsed = int(value or 200)
        except (TypeError, ValueError):
            parsed = 200
        return max(1, min(parsed, 1000))

    @staticmethod
    def _compose_error(result: CommandResult) -> OpsError:
        stderr = str(result.stderr or "")
        lower = stderr.lower()
        if result.not_found:
            return OpsError("docker_not_found", "docker binary not found")
        if "permission denied" in lower:
            return OpsError("docker_permission_denied", stderr[:240] or "Docker permission denied")
        if result.timed_out:
            return OpsError("docker_unreachable", "Docker Compose command timed out")
        if "not a docker command" in lower or "unknown command" in lower and "compose" in lower:
            return OpsError("compose_plugin_missing", stderr[:240] or "Docker Compose plugin unavailable")
        return OpsError("compose_file_invalid", stderr[:240] or "Docker Compose command failed")

    def _failed_result(self, result: CommandResult) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self._compose_error(result).to_dict(),
            "stderr": result.stderr[:500],
            "truncated": result.truncated,
        }


_default_docker_compose_service: DockerComposeService | None = None


def get_docker_compose_service() -> DockerComposeService:
    global _default_docker_compose_service
    if _default_docker_compose_service is None:
        _default_docker_compose_service = DockerComposeService()
    return _default_docker_compose_service
