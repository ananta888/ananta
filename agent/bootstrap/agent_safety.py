"""Composition root for Hub-owned agent safety controls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.agent_safety_admission_policy import AgentSafetyAdmissionPolicy
from agent.services.agent_safety_evaluation_service import AgentSafetyEvaluationService
from agent.services.agent_safety_ports import (
    CredentialLeaseRevocationPort,
    EgressFencePort,
    SandboxSafetyControlPort,
    UnavailableEgressFence,
    UnavailableSafetyControl,
)
from agent.services.agent_safety_recovery_service import AgentSafetyRecoveryService
from agent.services.agent_safety_retention_service import AgentSafetyRetentionService
from agent.services.agent_safety_runtime_adapters import (
    DockerAgentSafetyRuntime,
    DockerEgressFenceAdapter,
    DockerForensicSnapshotAdapter,
    DockerSandboxCleanupAdapter,
    DockerSandboxSafetyControlAdapter,
    HubCredentialLeaseAuthority,
)
from agent.services.agent_safety_service import AgentSafetyControlService
from agent.services.agent_safety_state_store import AgentSafetyStateStore
from agent.services.agent_safety_training_adapter import HubQueuedSafetyTrainingAdapter
from agent.services.ops_command_runner import get_default_command_runner


@dataclass(frozen=True, slots=True)
class AgentSafetyWiringStatus:
    ready: bool
    containment_available: bool
    reason_code: str | None


def initialize_agent_safety(
    app: Flask,
    *,
    sandbox_control: SandboxSafetyControlPort | None = None,
    egress_fence: EgressFencePort | None = None,
    credential_revocation: CredentialLeaseRevocationPort | None = None,
) -> AgentSafetyWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = AgentSafetyWiringStatus(False, False, "agent_safety_hub_role_required")
    else:
        try:
            store = AgentSafetyStateStore(
                Path(str(app.config.get("ANANTA_AGENT_SAFETY_STATE") or settings.agent_safety_state))
            )
            docker_runtime = None
            if sandbox_control is None and egress_fence is None and settings.agent_safety_runtime_adapter == "docker":
                docker_runtime = DockerAgentSafetyRuntime(
                    runner=get_default_command_runner(),
                    managed_sandboxes=[
                        item.strip() for item in settings.agent_safety_managed_sandboxes.split(",") if item.strip()
                    ],
                    snapshot_root=Path(settings.agent_safety_snapshot_root),
                )
                if not docker_runtime.ready():
                    docker_runtime = None
            sandbox = (
                sandbox_control
                or (DockerSandboxSafetyControlAdapter(docker_runtime) if docker_runtime is not None else None)
                or UnavailableSafetyControl()
            )
            egress = (
                egress_fence
                or (DockerEgressFenceAdapter(docker_runtime) if docker_runtime is not None else None)
                or UnavailableEgressFence()
            )
            lease_authority = HubCredentialLeaseAuthority(store)
            credentials = credential_revocation or lease_authority
            issued_lease_authority = (
                credentials
                if callable(getattr(credentials, "issue", None)) and callable(getattr(credentials, "verify", None))
                else None
            )
            admission = AgentSafetyAdmissionPolicy(store)
            key_material = hashlib.sha256(f"agent-safety-manifest-v1:{app.secret_key}".encode("utf-8")).digest()
            service = AgentSafetyControlService(
                store,
                manifest_signing_key=key_material,
                sandbox_control=sandbox,
                egress_fence=egress,
                credential_revocation=credentials,
                admission_policy=admission,
                credential_lease_authority=issued_lease_authority,
                forensic_snapshot=(
                    DockerForensicSnapshotAdapter(docker_runtime) if docker_runtime is not None else None
                ),
            )
            recovery = AgentSafetyRecoveryService(store)
            evaluation = AgentSafetyEvaluationService(
                store,
                series_signing_key=key_material,
                training_adapter=HubQueuedSafetyTrainingAdapter(store),
            )
        except (OSError, RuntimeError, ValueError):
            status = AgentSafetyWiringStatus(False, False, "agent_safety_configuration_invalid")
        else:
            app.extensions["agent_safety_state_store"] = store
            app.extensions["agent_safety_control_service"] = service
            app.extensions["agent_safety_recovery_service"] = recovery
            app.extensions["agent_safety_evaluation_service"] = evaluation
            if docker_runtime is not None:
                app.extensions["agent_safety_retention_service"] = AgentSafetyRetentionService(
                    store, cleanup=DockerSandboxCleanupAdapter(docker_runtime)
                )
            containment_available = docker_runtime is not None or all(
                value is not None for value in (sandbox_control, egress_fence, credential_revocation)
            )
            status = AgentSafetyWiringStatus(True, containment_available, None)
    app.extensions["agent_safety_wiring_status"] = status
    return status


__all__ = ["AgentSafetyWiringStatus", "initialize_agent_safety"]
