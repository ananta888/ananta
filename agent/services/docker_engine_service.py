from __future__ import annotations

import json
import platform
from typing import Any

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.services.ops_command_runner import CommandRunner, get_default_command_runner
from agent.services.ops_models import DockerContainerSummary, DockerEngineStatus, OpsActionResult, OpsError
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service


class DockerEngineService:
    def __init__(self, *, runner: CommandRunner | None = None, policy: OpsPolicyService | None = None) -> None:
        self._runner = runner or get_default_command_runner()
        self._policy = policy or get_ops_policy_service()

    def status(self) -> DockerEngineStatus:
        boundary = self._boundary()
        if boundary == "disabled":
            return DockerEngineStatus(
                available=False,
                boundary=boundary,
                platform_hint=self._platform_hint(),
                error=OpsError("docker_boundary_not_configured", "Docker Ops boundary is disabled"),
            )
        if not self._runner.exists("docker"):
            return DockerEngineStatus(False, boundary=boundary, platform_hint=self._platform_hint(), error=OpsError("docker_not_found", "docker binary not found"))
        version = self._runner.run(["docker", "version", "--format", "{{json .Server}}"], timeout_seconds=5)
        if version.returncode != 0:
            code = "docker_permission_denied" if "permission denied" in version.stderr.lower() else "docker_unreachable"
            return DockerEngineStatus(False, boundary=boundary, platform_hint=self._platform_hint(), error=OpsError(code, version.stderr[:240]))
        compose = self._runner.run(["docker", "compose", "version", "--format", "json"], timeout_seconds=5)
        docker_version = ""
        try:
            docker_version = str(json.loads(version.stdout or "{}").get("Version") or "")
        except Exception:
            docker_version = version.stdout.strip()[:80]
        return DockerEngineStatus(True, boundary=boundary, docker_version=docker_version, compose_available=compose.returncode == 0, platform_hint=self._platform_hint())

    def containers(self) -> list[DockerContainerSummary]:
        status = self.status()
        if not status.available:
            return []
        result = self._runner.run(
            ["docker", "ps", "--all", "--format", "{{json .}}"],
            timeout_seconds=10,
        )
        if result.returncode != 0:
            return []
        return [self._container_from_json(line) for line in result.stdout.splitlines() if line.strip()]

    def logs(self, container_id: str, *, tail: int = 200) -> dict[str, Any]:
        status = self.status()
        if not status.available:
            return {"ok": False, "error": status.error.to_dict() if status.error else None}
        safe_tail = max(1, min(int(tail or 200), 1000))
        result = self._runner.run(["docker", "logs", "--tail", str(safe_tail), str(container_id)], timeout_seconds=10)
        return {"ok": result.returncode == 0, "logs": result.stdout, "stderr": result.stderr[:500], "truncated": result.truncated}

    def inspect_light(self, container_id: str) -> dict[str, Any]:
        status = self.status()
        if not status.available:
            return {"ok": False, "error": status.error.to_dict() if status.error else None}
        result = self._runner.run(["docker", "inspect", str(container_id)], timeout_seconds=10)
        if result.returncode != 0:
            return {"ok": False, "error": OpsError("docker_unreachable", result.stderr[:240]).to_dict()}
        try:
            raw = json.loads(result.stdout or "[]")
        except Exception:
            raw = []
        item = raw[0] if raw else {}
        return {
            "ok": True,
            "inspect": {
                "id": str(item.get("Id") or "")[:12],
                "name": str(item.get("Name") or "").lstrip("/"),
                "image": str((item.get("Config") or {}).get("Image") or ""),
                "state": item.get("State") or {},
                "labels": (item.get("Config") or {}).get("Labels") or {},
            },
        }

    def action(self, container_id: str, action: str) -> OpsActionResult:
        if action not in {"start", "stop", "restart"}:
            return OpsActionResult(False, action, target_id=container_id, decision="policy_denied", error=OpsError("policy_denied", "unsupported docker action"))
        boundary_status = self.status()
        if not boundary_status.available:
            return OpsActionResult(False, action, target_id=container_id, error=boundary_status.error)
        decision = self._policy.evaluate("docker.container_action", action, target_id=container_id)
        if not decision.allowed:
            code = "approval_required" if decision.decision == "approval_required" else "policy_denied"
            approval_id = None
            if decision.decision == "approval_required":
                approval_id = self._policy.create_approval_request(
                    tool_name="docker.container_action",
                    action=action,
                    target_id=container_id,
                    arguments={"container_id": container_id, "action": action},
                )
            log_audit("ops_docker_action_blocked", {"action": action, "target_id": container_id, "decision": decision.decision, "approval_id": approval_id})
            return OpsActionResult(False, action, target_id=container_id, decision=decision.decision, approval_id=approval_id, error=OpsError(code, decision.reason_code))
        result = self._runner.run(["docker", action, container_id], timeout_seconds=30)
        ok = result.returncode == 0
        log_audit("ops_docker_action", {"action": action, "target_id": container_id, "ok": ok})
        return OpsActionResult(ok, action, target_id=container_id, error=None if ok else OpsError("docker_unreachable", result.stderr[:240]))

    def _container_from_json(self, line: str) -> DockerContainerSummary:
        try:
            item = json.loads(line)
        except Exception:
            item = {}
        labels = self._parse_labels(str(item.get("Labels") or ""))
        return DockerContainerSummary(
            id=str(item.get("ID") or "")[:12],
            name=str(item.get("Names") or ""),
            image=str(item.get("Image") or ""),
            status=str(item.get("Status") or ""),
            health=self._health_from_status(str(item.get("Status") or "")),
            ports=str(item.get("Ports") or ""),
            labels=labels,
            compose_project=labels.get("com.docker.compose.project", ""),
            uptime=str(item.get("RunningFor") or ""),
        )

    @staticmethod
    def _parse_labels(raw: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for part in raw.split(","):
            key, _, value = part.partition("=")
            if key.strip():
                labels[key.strip()] = value.strip()
        return labels

    @staticmethod
    def _health_from_status(status: str) -> str:
        lower = status.lower()
        if "healthy" in lower:
            return "healthy"
        if "unhealthy" in lower:
            return "unhealthy"
        return ""

    def _boundary(self) -> str:
        if not has_app_context():
            return "disabled"
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        docker_ops = dict(cfg.get("docker_ops") or {})
        return str(docker_ops.get("boundary") or "disabled").strip().lower()

    @staticmethod
    def _platform_hint() -> str:
        return f"{platform.system()} {platform.release()}"


_default_docker_engine_service: DockerEngineService | None = None


def get_docker_engine_service() -> DockerEngineService:
    global _default_docker_engine_service
    if _default_docker_engine_service is None:
        _default_docker_engine_service = DockerEngineService()
    return _default_docker_engine_service
