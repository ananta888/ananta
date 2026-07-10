from __future__ import annotations

import json
from typing import Any

from agent.common.audit import log_audit
from agent.services.docker_engine_service import DockerEngineService, get_docker_engine_service
from agent.services.ops_command_runner import CommandRunner, get_default_command_runner
from agent.services.ops_models import ComposeProjectSummary, ComposeServiceStatus, OpsActionResult, OpsError
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import ComposeProjectRef, OpsRegistryService, get_ops_registry_service


class DockerComposeService:
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
        ref = self._registry.resolve_compose_project(project_id)
        if ref is None:
            return ComposeProjectSummary(
                project_id=str(project_id or ""),
                name="",
                project_directory="",
                compose_files=[],
                error=OpsError("compose_project_not_registered", "compose project is not registered"),
            )
        summary = self._summary_from_ref(ref)
        docker_status = self._docker.status()
        if not docker_status.available:
            return ComposeProjectSummary(**{**summary.to_dict(), "error": docker_status.error})
        ps = self._compose(ref, ["ps", "--format", "json"], timeout_seconds=10)
        if ps.returncode != 0:
            code = "compose_plugin_missing" if "compose" in ps.stderr.lower() else "compose_file_invalid"
            return ComposeProjectSummary(**{**summary.to_dict(), "error": OpsError(code, ps.stderr[:240])})
        services = self._parse_ps(ps.stdout)
        return ComposeProjectSummary(**{**summary.to_dict(), "services": services})

    def config(self, project_id: str) -> dict[str, Any]:
        ref = self._registry.resolve_compose_project(project_id)
        if ref is None:
            return {"ok": False, "error": OpsError("compose_project_not_registered", "compose project is not registered").to_dict()}
        result = self._compose(ref, ["config"], timeout_seconds=10)
        return {"ok": result.returncode == 0, "config": result.stdout, "stderr": result.stderr[:500], "truncated": result.truncated}

    def logs(self, project_id: str, *, service: str | None = None, tail: int = 200) -> dict[str, Any]:
        ref = self._registry.resolve_compose_project(project_id)
        if ref is None:
            return {"ok": False, "error": OpsError("compose_project_not_registered", "compose project is not registered").to_dict()}
        safe_tail = max(1, min(int(tail or 200), 1000))
        args = ["logs", "--no-color", "--tail", str(safe_tail)]
        if service:
            args.append(str(service))
        result = self._compose(ref, args, timeout_seconds=10)
        return {"ok": result.returncode == 0, "logs": result.stdout, "stderr": result.stderr[:500], "truncated": result.truncated}

    def action(self, project_id: str, action: str) -> OpsActionResult:
        ref = self._registry.resolve_compose_project(project_id)
        if ref is None:
            return OpsActionResult(False, action, target_id=project_id, error=OpsError("compose_project_not_registered", "compose project is not registered"))
        if action not in {"up", "restart", "down"}:
            return OpsActionResult(False, action, target_id=project_id, decision="policy_denied", error=OpsError("policy_denied", "unsupported compose action"))
        if action == "down_volumes":
            return OpsActionResult(False, action, target_id=project_id, decision="policy_denied", error=OpsError("policy_denied", "compose down --volumes is denied"))
        docker_status = self._docker.status()
        if not docker_status.available:
            return OpsActionResult(False, action, target_id=project_id, error=docker_status.error)
        decision = self._policy.evaluate("compose.project_action", action, target_id=project_id)
        if not decision.allowed:
            code = "approval_required" if decision.decision == "approval_required" else "policy_denied"
            approval_id = None
            if decision.decision == "approval_required":
                approval_id = self._policy.create_approval_request(
                    tool_name="compose.project_action",
                    action=action,
                    target_id=project_id,
                    arguments={"project_id": project_id, "action": action},
                )
            log_audit("ops_compose_action_blocked", {"action": action, "project_id": project_id, "decision": decision.decision, "approval_id": approval_id})
            return OpsActionResult(False, action, target_id=project_id, decision=decision.decision, approval_id=approval_id, error=OpsError(code, decision.reason_code))
        args = ["up", "-d"] if action == "up" else [action]
        result = self._compose(ref, args, timeout_seconds=60)
        ok = result.returncode == 0
        log_audit("ops_compose_action", {"action": action, "project_id": project_id, "ok": ok})
        return OpsActionResult(ok, action, target_id=project_id, error=None if ok else OpsError("compose_file_invalid", result.stderr[:240]))

    def _summary_from_ref(self, ref: ComposeProjectRef) -> ComposeProjectSummary:
        return ComposeProjectSummary(
            project_id=ref.project_id,
            name=ref.name,
            project_directory=str(ref.project_directory),
            compose_files=[str(path) for path in ref.compose_files],
            profiles=list(ref.profiles),
            marker=ref.marker,
            category=ref.category,
            allowed_actions=list(ref.allowed_actions),
        )

    def _compose(self, ref: ComposeProjectRef, args: list[str], *, timeout_seconds: int):
        cmd = ["docker", "compose"]
        for compose_file in ref.compose_files:
            cmd.extend(["-f", str(compose_file)])
        cmd.extend(args)
        env = {"COMPOSE_PROFILES": ",".join(ref.profiles)} if ref.profiles else None
        return self._runner.run(cmd, cwd=ref.project_directory, timeout_seconds=timeout_seconds, env=env)

    @staticmethod
    def _parse_ps(stdout: str) -> list[ComposeServiceStatus]:
        services: list[ComposeServiceStatus] = []
        raw = stdout.strip()
        if not raw:
            return services
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
        except Exception:
            items = []
            for line in raw.splitlines():
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
        for item in items:
            if not isinstance(item, dict):
                continue
            services.append(
                ComposeServiceStatus(
                    name=str(item.get("Service") or item.get("Name") or ""),
                    state=str(item.get("State") or item.get("Status") or ""),
                    health=str(item.get("Health") or ""),
                    exit_code=str(item.get("ExitCode") or ""),
                    ports=str(item.get("Publishers") or item.get("Ports") or ""),
                )
            )
        return services


_default_docker_compose_service: DockerComposeService | None = None


def get_docker_compose_service() -> DockerComposeService:
    global _default_docker_compose_service
    if _default_docker_compose_service is None:
        _default_docker_compose_service = DockerComposeService()
    return _default_docker_compose_service
