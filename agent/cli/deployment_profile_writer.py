from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DeploymentTarget = str


@dataclass(frozen=True)
class DeploymentWriteResult:
    path: str
    backup_path: str | None


def build_deployment_profile(
    *,
    runtime_mode: str,
    runtime_profile: str,
    governance_mode: str,
    target: DeploymentTarget,
    config_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_target = str(target or "").strip().lower()
    if normalized_target not in {"docker-compose", "podman"}:
        raise ValueError(f"unsupported deployment target: {target}")

    stronger_isolation = runtime_mode in {"sandbox", "strict"}
    container_profile = _container_hardening_profile(runtime_mode=runtime_mode, runtime_profile=runtime_profile)
    return {
        "schema": "ananta.deployment-profile.v1",
        "version": "1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": normalized_target,
        "runtime_mode": runtime_mode,
        "runtime_profile": runtime_profile,
        "governance_mode": governance_mode,
        "local_dev_default_is_non_container": True,
        "isolation_level": "stronger" if stronger_isolation else "standard",
        "container_hardening_profile": container_profile,
        "notes": [
            "Container deployment is optional; local-dev remains non-container by default.",
            "Sandbox and strict modes are stronger isolation choices.",
            "Container hardening profile is declarative and should be mapped to compose/k8s runtime settings.",
        ],
        "examples": _deployment_examples(normalized_target, runtime_mode),
        "config_patch": dict(config_patch or {}),
    }


def write_deployment_profile(
    *,
    path: Path,
    payload: dict[str, Any],
    overwrite_confirmed: bool,
    backup_existing: bool,
) -> DeploymentWriteResult:
    backup_path: Path | None = None
    if path.exists():
        if overwrite_confirmed:
            pass
        elif backup_existing:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileExistsError(
                f"deployment profile already exists: {path} (use explicit overwrite confirmation or backup)"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return DeploymentWriteResult(path=str(path), backup_path=str(backup_path) if backup_path else None)


def _deployment_examples(target: str, runtime_mode: str) -> list[str]:
    if target == "docker-compose":
        if runtime_mode == "strict":
            return [
                "docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build"
            ]
        return ["docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build"]
    if runtime_mode == "strict":
        return [
            "podman compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build"
        ]
    return ["podman compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build"]


def _container_hardening_profile(*, runtime_mode: str, runtime_profile: str) -> dict[str, Any]:
    mode = str(runtime_mode or "").strip().lower()
    profile = str(runtime_profile or "").strip().lower()
    if mode in {"sandbox", "strict"}:
        return {
            "profile_id": "kritis-hardened-v1",
            "runtime_mode": mode,
            "runtime_profile": profile,
            "rootless_required": True,
            "read_only_rootfs": True,
            "drop_all_capabilities": True,
            "allow_privilege_escalation": False,
            "seccomp_profile": "runtime/default",
            "apparmor_profile": "docker-default",
            "network_policy_class": "restricted-egress",
            "workspace_mount_mode": "bounded_rw",
            "tmpfs_mounts": ["/tmp", "/run"],
        }
    return {
        "profile_id": "default-dev-v1",
        "runtime_mode": mode or "local-dev",
        "runtime_profile": profile,
        "rootless_required": False,
        "read_only_rootfs": False,
        "drop_all_capabilities": False,
        "allow_privilege_escalation": True,
        "network_policy_class": "default",
        "workspace_mount_mode": "standard_rw",
        "tmpfs_mounts": [],
    }
