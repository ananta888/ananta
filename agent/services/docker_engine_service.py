from __future__ import annotations

import json
import os
import platform
import shlex
import time
from typing import Any

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.services.ops_command_runner import CommandResult, CommandRunner, get_default_command_runner
from agent.services.ops_models import DockerContainerSummary, DockerEngineStatus, OpsActionResult, OpsError
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import OpsRegistryService, get_ops_registry_service

_CONTAINER_READ_ACTIONS = ("logs", "inspect_light", "stats")
_CONTAINER_MUTATIONS = frozenset({"start", "stop", "restart"})


class DockerEngineService:
    """Safe hub-side Docker CLI adapter.

    The service never accepts an arbitrary Docker target for a read or write.
    Client identifiers must first resolve to an exact item from the bounded
    ``docker ps --all`` snapshot. Mutations additionally require a server-owned
    container or Compose-project registration and pass through the central Ops
    policy/approval lifecycle.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        policy: OpsPolicyService | None = None,
        registry: OpsRegistryService | None = None,
    ) -> None:
        self._runner = runner or get_default_command_runner()
        self._policy = policy or get_ops_policy_service()
        self._registry = registry or get_ops_registry_service()
        self._status_cache: tuple[str, float, DockerEngineStatus] | None = None

    def status(self) -> DockerEngineStatus:
        boundary = self._boundary()
        now = time.monotonic()
        cached = self._status_cache
        if cached is not None and cached[0] == boundary and cached[1] > now:
            return cached[2]
        result = self._load_status(boundary)
        self._status_cache = (boundary, now + 2.0, result)
        return result

    def _load_status(self, boundary: str) -> DockerEngineStatus:
        if boundary == "disabled":
            return DockerEngineStatus(
                available=False,
                boundary=boundary,
                platform_hint=self._platform_hint(),
                error=OpsError("docker_boundary_not_configured", "Docker Ops boundary is disabled"),
            )
        if boundary != "hub_cli":
            return DockerEngineStatus(
                available=False,
                boundary=boundary,
                platform_hint=self._platform_hint(),
                error=OpsError("docker_boundary_not_configured", "Unsupported Docker Ops boundary"),
            )
        if not self._runner.exists("docker"):
            return DockerEngineStatus(
                False,
                boundary=boundary,
                platform_hint=self._platform_hint(),
                error=OpsError("docker_not_found", "docker binary not found"),
            )
        version = self._runner.run(["docker", "version", "--format", "{{json .Server}}"], timeout_seconds=5)
        if version.returncode != 0:
            return DockerEngineStatus(
                False,
                boundary=boundary,
                platform_hint=self._platform_hint(),
                error=self._command_error(version),
            )
        compose = self._runner.run(["docker", "compose", "version", "--format", "json"], timeout_seconds=5)
        server = self._json_object(version.stdout)
        docker_version = str(server.get("Version") or version.stdout.strip())[:80]
        return DockerEngineStatus(
            True,
            boundary=boundary,
            docker_version=docker_version,
            compose_available=compose.returncode == 0,
            platform_hint=self._platform_hint(),
            engine=self._version_summary(server),
        )

    def info(self) -> dict[str, Any]:
        unavailable = self._unavailable_result()
        if unavailable:
            return unavailable
        result = self._runner.run(["docker", "info", "--format", "{{json .}}"], timeout_seconds=10)
        if result.returncode != 0:
            return self._failed_result(result)
        raw = self._json_object(result.stdout)
        return {
            "ok": True,
            "info": {
                "id": str(raw.get("ID") or ""),
                "name": str(raw.get("Name") or ""),
                "server_version": str(raw.get("ServerVersion") or ""),
                "operating_system": str(raw.get("OperatingSystem") or ""),
                "os_type": str(raw.get("OSType") or ""),
                "architecture": str(raw.get("Architecture") or ""),
                "kernel_version": str(raw.get("KernelVersion") or ""),
                "driver": str(raw.get("Driver") or ""),
                "containerd_version": self._component_version(raw, "containerd"),
                "containers": self._int(raw.get("Containers")),
                "containers_running": self._int(raw.get("ContainersRunning")),
                "containers_paused": self._int(raw.get("ContainersPaused")),
                "containers_stopped": self._int(raw.get("ContainersStopped")),
                "images": self._int(raw.get("Images")),
                "cpus": self._int(raw.get("NCPU")),
                "memory_bytes": self._int(raw.get("MemTotal")),
                "memory_limit": bool(raw.get("MemoryLimit", False)),
                "swap_limit": bool(raw.get("SwapLimit", False)),
                "live_restore_enabled": bool(raw.get("LiveRestoreEnabled", False)),
                "security_options": [str(item) for item in list(raw.get("SecurityOptions") or [])],
                "warnings": [str(item)[:500] for item in list(raw.get("Warnings") or [])[:20]],
            },
            "truncated": result.truncated,
        }

    def containers(self) -> list[DockerContainerSummary]:
        items, _, _ = self._container_items()
        return items

    def container_snapshot(self) -> dict[str, Any]:
        items, error, truncated = self._container_items()
        return {
            "ok": error is None,
            "items": [item.to_dict() for item in items],
            "count": len(items),
            "truncated": truncated,
            "error": error.to_dict() if error else None,
        }

    def _container_items(self) -> tuple[list[DockerContainerSummary], OpsError | None, bool]:
        status = self.status()
        if not status.available:
            return [], status.error, False
        result = self._runner.run(
            ["docker", "ps", "--all", "--size", "--format", "{{json .}}"],
            timeout_seconds=10,
        )
        if result.returncode != 0:
            return [], self._command_error(result), result.truncated
        items = [self._container_from_json(line) for line in result.stdout.splitlines() if line.strip()]
        return items, None, result.truncated

    def logs(
        self,
        container_id: str,
        *,
        tail: int = 200,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        target, error = self._resolve_container(container_id)
        if error:
            return {"ok": False, "error": error.to_dict()}
        safe_tail = self._bounded_tail(tail)
        args = ["docker", "logs", "--tail", str(safe_tail)]
        if timestamps:
            args.append("--timestamps")
        args.append(target.id)
        result = self._runner.run(args, timeout_seconds=10)
        if result.returncode != 0:
            return self._failed_result(result)
        # Docker emits some log drivers on stderr. Keep both streams explicit;
        # neither stream is ever allowed to exceed CommandRunner's cap.
        return {
            "ok": True,
            "container_id": target.id,
            "logs": result.stdout,
            "stderr": result.stderr,
            "tail": safe_tail,
            "truncated": result.truncated,
        }

    def inspect_light(self, container_id: str) -> dict[str, Any]:
        target, error = self._resolve_container(container_id)
        if error:
            return {"ok": False, "error": error.to_dict()}
        result = self._runner.run(["docker", "inspect", target.id], timeout_seconds=10)
        if result.returncode != 0:
            return self._failed_result(result)
        raw = self._json_array(result.stdout)
        item = raw[0] if raw and isinstance(raw[0], dict) else {}
        config = dict(item.get("Config") or {})
        host = dict(item.get("HostConfig") or {})
        state = dict(item.get("State") or {})
        network_settings = dict(item.get("NetworkSettings") or {})
        return {
            "ok": True,
            "inspect": {
                "id": str(item.get("Id") or "")[:12],
                "name": str(item.get("Name") or "").lstrip("/"),
                "created_at": str(item.get("Created") or ""),
                "image": str(config.get("Image") or ""),
                "image_id": str(item.get("Image") or "")[:19],
                "platform": str(item.get("Platform") or ""),
                "path": str(item.get("Path") or ""),
                # Args and environment are deliberately omitted: both commonly
                # carry credentials. The executable path still explains the
                # process without turning inspect-light into a secret endpoint.
                "labels": self._safe_labels(dict(config.get("Labels") or {})),
                "state": self._safe_state(state),
                "resources": self._safe_resources(host),
                "restart_count": self._int(item.get("RestartCount")),
                "restart_policy": dict(host.get("RestartPolicy") or {}),
                "mounts": self._safe_mounts(item.get("Mounts")),
                "networks": self._safe_networks(network_settings.get("Networks")),
                "ports": dict(network_settings.get("Ports") or {}),
                "allowed_actions": target.allowed_actions,
            },
            "truncated": result.truncated,
        }

    def stats(self, container_id: str) -> dict[str, Any]:
        target, error = self._resolve_container(container_id)
        if error:
            return {"ok": False, "error": error.to_dict()}
        result = self._runner.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", target.id],
            timeout_seconds=15,
        )
        if result.returncode != 0:
            return self._failed_result(result)
        raw = self._json_object(result.stdout.splitlines()[0] if result.stdout.strip() else "")
        return {
            "ok": True,
            "container_id": target.id,
            "stats": {
                "cpu_percent": str(raw.get("CPUPerc") or ""),
                "memory_usage": str(raw.get("MemUsage") or ""),
                "memory_percent": str(raw.get("MemPerc") or ""),
                "network_io": str(raw.get("NetIO") or ""),
                "block_io": str(raw.get("BlockIO") or ""),
                "pids": str(raw.get("PIDs") or ""),
            },
            "truncated": result.truncated,
        }

    def images(self) -> dict[str, Any]:
        return self._list_resource(
            ["docker", "image", "ls", "--all", "--digests", "--format", "{{json .}}"],
            fields=("ID", "Repository", "Tag", "Digest", "CreatedSince", "CreatedAt", "Size", "Containers"),
        )

    def networks(self) -> dict[str, Any]:
        return self._list_resource(
            ["docker", "network", "ls", "--no-trunc", "--format", "{{json .}}"],
            fields=("ID", "Name", "Driver", "Scope", "Internal", "IPv6", "CreatedAt", "Labels"),
        )

    def volumes(self) -> dict[str, Any]:
        # Mountpoints are intentionally not requested: they disclose host paths
        # and are unnecessary for an operator overview.
        return self._list_resource(
            ["docker", "volume", "ls", "--format", "{{json .}}"],
            fields=("Name", "Driver", "Scope", "CreatedAt", "Labels", "Links", "Size"),
        )

    def disk_usage(self) -> dict[str, Any]:
        return self._list_resource(
            ["docker", "system", "df", "--format", "{{json .}}"],
            fields=("Type", "TotalCount", "Active", "Size", "Reclaimable"),
        )

    def action(self, container_id: str, action: str, *, approval_id: str | None = None) -> OpsActionResult:
        action = str(action or "").strip().lower()
        if action not in _CONTAINER_MUTATIONS:
            return OpsActionResult(
                False,
                action,
                target_id=container_id,
                decision="policy_denied",
                error=OpsError("policy_denied", "unsupported docker action"),
            )
        target, error = self._resolve_container(container_id)
        if error:
            return OpsActionResult(False, action, target_id=container_id, error=error)
        if action not in set(target.allowed_actions):
            log_audit(
                "ops_docker_action_blocked",
                {
                    "action": action,
                    "target_id": target.id,
                    "decision": "policy_denied",
                    "reason": "container_not_managed",
                },
            )
            return OpsActionResult(
                False,
                action,
                target_id=target.id,
                decision="policy_denied",
                error=OpsError("policy_denied", "container is not registered for this action"),
            )
        arguments = {"container_id": target.id, "action": action}
        decision = self._policy.authorize(
            "docker.container_action",
            action,
            target_id=target.id,
            arguments=arguments,
            approval_id=approval_id,
        )
        if not decision.allowed:
            code = "approval_required" if decision.decision == "approval_required" else "policy_denied"
            attempted_approval_id = str(decision.metadata.get("approval_id") or approval_id or "") or None
            response_approval_id: str | None = None
            if decision.decision == "approval_required":
                response_approval_id = attempted_approval_id or self._policy.create_approval_request(
                    tool_name="docker.container_action", action=action, target_id=target.id, arguments=arguments
                )
            log_audit(
                "ops_docker_action_blocked",
                {
                    "action": action,
                    "target_id": target.id,
                    "decision": decision.decision,
                    "approval_id": attempted_approval_id or response_approval_id,
                },
            )
            return OpsActionResult(
                False,
                action,
                target_id=target.id,
                decision=decision.decision,
                approval_id=response_approval_id,
                error=OpsError(code, decision.reason_code),
            )
        result = self._runner.run(["docker", action, target.id], timeout_seconds=30)
        ok = result.returncode == 0
        if ok:
            self._policy.consume_approval(approval_id)
        log_audit(
            "ops_docker_action",
            {"action": action, "target_id": target.id, "container_name": target.name, "ok": ok},
        )
        return OpsActionResult(
            ok,
            action,
            target_id=target.id,
            error=None if ok else self._command_error(result),
            approval_id=approval_id,
            metadata={"container_name": target.name, "output": result.stdout.strip()[:240]},
        )

    def _resolve_container(self, requested_id: str) -> tuple[DockerContainerSummary | None, OpsError | None]:
        wanted = str(requested_id or "").strip()
        if not wanted:
            return None, OpsError("docker_container_not_registered", "container id is required")
        status = self.status()
        if not status.available:
            return None, status.error
        # Exact matching only. Prefixes not previously returned by this API and
        # daemon-side fuzzy name resolution are deliberately not accepted.
        items, list_error, _ = self._container_items()
        if list_error:
            return None, list_error
        target = next((item for item in items if wanted in {item.id, item.name}), None)
        if target is None:
            return None, OpsError("docker_container_not_registered", "container is not registered")
        return target, None

    def _container_from_json(self, line: str) -> DockerContainerSummary:
        item = self._json_object(line)
        raw_labels = self._parse_labels(str(item.get("Labels") or ""))
        container_id = str(item.get("ID") or "")[:12]
        name = str(item.get("Names") or "")
        compose_project = raw_labels.get("com.docker.compose.project", "")
        managed_actions = self._registry.container_allowed_actions(
            container_id=container_id,
            name=name,
            compose_project=compose_project,
        )
        return DockerContainerSummary(
            id=container_id,
            name=name,
            image=str(item.get("Image") or ""),
            status=str(item.get("Status") or ""),
            health=self._health_from_status(str(item.get("Status") or "")),
            ports=str(item.get("Ports") or ""),
            labels=self._safe_labels(raw_labels),
            compose_project=compose_project,
            uptime=str(item.get("RunningFor") or ""),
            state=str(item.get("State") or ""),
            command=self._safe_command(item.get("Command")),
            created_at=str(item.get("CreatedAt") or ""),
            size=str(item.get("Size") or ""),
            networks=self._csv(item.get("Networks")),
            mounts=self._csv(item.get("Mounts")),
            registered=True,
            managed=bool(set(managed_actions) & _CONTAINER_MUTATIONS),
            allowed_actions=sorted(set(_CONTAINER_READ_ACTIONS) | set(managed_actions)),
        )

    def _list_resource(self, command: list[str], *, fields: tuple[str, ...]) -> dict[str, Any]:
        unavailable = self._unavailable_result()
        if unavailable:
            return unavailable
        result = self._runner.run(command, timeout_seconds=15)
        if result.returncode != 0:
            return self._failed_result(result)
        items: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            raw = self._json_object(line)
            if raw:
                item = {self._field_key(field): raw.get(field) for field in fields}
                if "labels" in item:
                    item["labels"] = self._safe_labels(self._parse_labels(str(item.get("labels") or "")))
                items.append(item)
        return {"ok": True, "items": items, "count": len(items), "truncated": result.truncated}

    def _unavailable_result(self) -> dict[str, Any] | None:
        status = self.status()
        if status.available:
            return None
        return {"ok": False, "error": status.error.to_dict() if status.error else None}

    def _failed_result(self, result: CommandResult) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self._command_error(result).to_dict(),
            "stderr": result.stderr[:500],
            "truncated": result.truncated,
        }

    @staticmethod
    def _command_error(result: CommandResult) -> OpsError:
        stderr = str(result.stderr or "")
        lower = stderr.lower()
        if result.not_found:
            return OpsError("docker_not_found", "docker binary not found")
        if "permission denied" in lower:
            return OpsError("docker_permission_denied", stderr[:240] or "Docker permission denied")
        if result.timed_out:
            return OpsError("docker_unreachable", "Docker command timed out")
        return OpsError("docker_unreachable", stderr[:240] or "Docker command failed")

    @staticmethod
    def _safe_state(state: dict[str, Any]) -> dict[str, Any]:
        health = dict(state.get("Health") or {})
        return {
            "status": str(state.get("Status") or ""),
            "running": bool(state.get("Running", False)),
            "paused": bool(state.get("Paused", False)),
            "restarting": bool(state.get("Restarting", False)),
            "oom_killed": bool(state.get("OOMKilled", False)),
            "dead": bool(state.get("Dead", False)),
            "pid": DockerEngineService._int(state.get("Pid")),
            "exit_code": DockerEngineService._int(state.get("ExitCode")),
            "error": str(state.get("Error") or "")[:500],
            "started_at": str(state.get("StartedAt") or ""),
            "finished_at": str(state.get("FinishedAt") or ""),
            "health": {
                "status": str(health.get("Status") or ""),
                "failing_streak": DockerEngineService._int(health.get("FailingStreak")),
            },
        }

    @staticmethod
    def _safe_resources(host: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_bytes": DockerEngineService._int(host.get("Memory")),
            "memory_swap_bytes": DockerEngineService._int(host.get("MemorySwap")),
            "memory_reservation_bytes": DockerEngineService._int(host.get("MemoryReservation")),
            "nano_cpus": DockerEngineService._int(host.get("NanoCpus")),
            "cpu_shares": DockerEngineService._int(host.get("CpuShares")),
            "cpuset_cpus": str(host.get("CpusetCpus") or ""),
            "pids_limit": DockerEngineService._int(host.get("PidsLimit")),
            "oom_kill_disable": bool(host.get("OomKillDisable", False)),
            "shm_size_bytes": DockerEngineService._int(host.get("ShmSize")),
            "read_only_rootfs": bool(host.get("ReadonlyRootfs", False)),
            "privileged": bool(host.get("Privileged", False)),
        }

    @staticmethod
    def _safe_mounts(raw_mounts: Any) -> list[dict[str, Any]]:
        mounts: list[dict[str, Any]] = []
        for raw in list(raw_mounts or []):
            if not isinstance(raw, dict):
                continue
            mounts.append(
                {
                    "type": str(raw.get("Type") or ""),
                    "name": str(raw.get("Name") or ""),
                    "destination": str(raw.get("Destination") or ""),
                    "driver": str(raw.get("Driver") or ""),
                    "mode": str(raw.get("Mode") or ""),
                    "rw": bool(raw.get("RW", False)),
                    "propagation": str(raw.get("Propagation") or ""),
                }
            )
        return mounts

    @staticmethod
    def _safe_networks(raw_networks: Any) -> dict[str, Any]:
        networks: dict[str, Any] = {}
        for name, raw in dict(raw_networks or {}).items():
            item = dict(raw or {})
            networks[str(name)] = {
                "network_id": str(item.get("NetworkID") or "")[:12],
                "endpoint_id": str(item.get("EndpointID") or "")[:12],
                "gateway": str(item.get("Gateway") or ""),
                "ip_address": str(item.get("IPAddress") or ""),
                "global_ipv6_address": str(item.get("GlobalIPv6Address") or ""),
                "mac_address": str(item.get("MacAddress") or ""),
                "aliases": [str(value) for value in list(item.get("Aliases") or [])],
            }
        return networks

    @staticmethod
    def _version_summary(server: dict[str, Any]) -> dict[str, Any]:
        return {
            "api_version": str(server.get("ApiVersion") or ""),
            "minimum_api_version": str(server.get("MinAPIVersion") or ""),
            "git_commit": str(server.get("GitCommit") or ""),
            "go_version": str(server.get("GoVersion") or ""),
            "os": str(server.get("Os") or ""),
            "architecture": str(server.get("Arch") or ""),
            "experimental": bool(server.get("Experimental", False)),
        }

    @staticmethod
    def _component_version(raw: dict[str, Any], name: str) -> str:
        for item in list(raw.get("Components") or []):
            if isinstance(item, dict) and str(item.get("Name") or "") == name:
                return str(item.get("Version") or "")
        return ""

    @staticmethod
    def _json_object(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _json_array(raw: str) -> list[Any]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _parse_labels(raw: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for part in raw.split(","):
            key, _, value = part.partition("=")
            if key.strip():
                labels[key.strip()] = value.strip()
        return labels

    @staticmethod
    def _safe_labels(labels: dict[str, Any]) -> dict[str, str]:
        safe: dict[str, str] = {}
        allowed_prefixes = ("com.docker.compose.", "org.opencontainers.image.")
        sensitive_tokens = ("secret", "token", "password", "credential", "private", "auth")
        path_labels = ("config_files", "working_dir", "environment_file")
        for raw_key, raw_value in labels.items():
            key = str(raw_key or "")
            lower = key.lower()
            if not key.startswith(allowed_prefixes):
                continue
            if any(token in lower for token in (*sensitive_tokens, *path_labels)):
                continue
            safe[key[:200]] = str(raw_value or "")[:500]
            if len(safe) >= 100:
                break
        return safe

    @staticmethod
    def _safe_command(value: Any) -> str:
        try:
            parts = shlex.split(str(value or ""))
        except ValueError:
            parts = []
        return parts[0][:240] if parts else ""

    @staticmethod
    def _health_from_status(status: str) -> str:
        lower = status.lower()
        if "unhealthy" in lower:
            return "unhealthy"
        if "healthy" in lower:
            return "healthy"
        if "health: starting" in lower:
            return "starting"
        return ""

    @staticmethod
    def _csv(value: Any) -> list[str]:
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _bounded_tail(value: Any) -> int:
        try:
            parsed = int(value or 200)
        except (TypeError, ValueError):
            parsed = 200
        return max(1, min(parsed, 1000))

    @staticmethod
    def _snake_case(value: str) -> str:
        chars: list[str] = []
        for index, char in enumerate(value):
            if char.isupper() and index:
                chars.append("_")
            chars.append(char.lower())
        return "".join(chars)

    @staticmethod
    def _field_key(value: str) -> str:
        return {"ID": "id", "IPv6": "ipv6"}.get(value, DockerEngineService._snake_case(value))

    def _boundary(self) -> str:
        configured = ""
        if has_app_context():
            cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
            docker_ops = dict(cfg.get("docker_ops") or {})
            configured = str(docker_ops.get("boundary") or "").strip()
        env_boundary = str(
            os.environ.get("ANANTA_DOCKER_OPS_BOUNDARY") or os.environ.get("DOCKER_OPS_BOUNDARY") or ""
        ).strip()
        return (configured or env_boundary or "disabled").lower()

    @staticmethod
    def _platform_hint() -> str:
        return f"{platform.system()} {platform.release()}"


_default_docker_engine_service: DockerEngineService | None = None


def get_docker_engine_service() -> DockerEngineService:
    global _default_docker_engine_service
    if _default_docker_engine_service is None:
        _default_docker_engine_service = DockerEngineService()
    return _default_docker_engine_service
