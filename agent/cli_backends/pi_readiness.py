"""Content-free Pi readiness from existing Worker observations, never a grant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.cli_backends.pi_configuration import PI_VERSION


def pi_readiness_projection(*, installation: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
    installed = installation.get("installed") is True
    probe = installation.get("version_probe")
    verified = (
        installed and installation.get("status") == "ready" and installation.get("version") == PI_VERSION
        and isinstance(probe, Mapping) and type(probe.get("rc")) is int
        and dict(probe) == {"rc": 0, "stdout": PI_VERSION, "stderr": ""}
    )
    native = _pi_registered(runtime)
    state = (
        "not_installed" if not installed else "version_unverified" if not verified
        else "native_not_registered" if not native else "ready_for_assignment"
    )
    return {
        "schema": "ananta.pi-worker-readiness.v1", "client_id": "pi", "free_class": "open_source_byok",
        "state": state, "installed": installed, "expected_version": PI_VERSION,
        "verified_version": PI_VERSION if verified else None,
        "native_registered": native,
        "auth_status": "profile_configured_unverified" if native else "task_profile_required",
        "inference_verified": False, "inference_cost": "provider_dependent",
        "model_selection": "hub_task_profile", "execution_scope": "hub_native_task",
        "requires_hub_assignment": True, "global_auto_routing": False,
        "capabilities": {
            "headless": True, "structured_output": True, "tools": False, "mcp": False,
            "workspace_write": False, "session_resume": False,
        },
    }


def _pi_registered(runtime: Mapping[str, Any]) -> bool:
    capabilities, targets = runtime.get("capabilities"), runtime.get("runtime_targets")
    if (
        not isinstance(capabilities, (list, tuple)) or not isinstance(targets, (list, tuple))
        or len(capabilities) > 128 or len(targets) > 128
        or not {"coding.agent.pi", "workflow.adapter.native"}.issubset(
            item for item in capabilities if isinstance(item, str)
        )
    ):
        return False
    return any(
        isinstance(target, Mapping) and target.get("runtime_id") == "ananta-native"
        and target.get("runtime_kind") == "docker_container" and target.get("adapter_id") == "native"
        and isinstance(target.get("allowed_capabilities"), (list, tuple))
        and "coding.agent.pi" in target["allowed_capabilities"]
        for target in targets
    )
