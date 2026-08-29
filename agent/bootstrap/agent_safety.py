"""Composition root for Hub-owned agent safety controls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.agent_safety_evaluation_service import AgentSafetyEvaluationService
from agent.services.agent_safety_ports import (
    CredentialLeaseRevocationPort,
    EgressFencePort,
    SandboxSafetyControlPort,
    UnavailableCredentialRevocation,
    UnavailableEgressFence,
    UnavailableSafetyControl,
)
from agent.services.agent_safety_recovery_service import AgentSafetyRecoveryService
from agent.services.agent_safety_service import AgentSafetyControlService
from agent.services.agent_safety_state_store import AgentSafetyStateStore


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
            sandbox = sandbox_control or UnavailableSafetyControl()
            egress = egress_fence or UnavailableEgressFence()
            credentials = credential_revocation or UnavailableCredentialRevocation()
            key_material = hashlib.sha256(f"agent-safety-manifest-v1:{app.secret_key}".encode("utf-8")).digest()
            service = AgentSafetyControlService(
                store,
                manifest_signing_key=key_material,
                sandbox_control=sandbox,
                egress_fence=egress,
                credential_revocation=credentials,
            )
            recovery = AgentSafetyRecoveryService(store)
            evaluation = AgentSafetyEvaluationService(store, series_signing_key=key_material)
        except (OSError, RuntimeError, ValueError):
            status = AgentSafetyWiringStatus(False, False, "agent_safety_configuration_invalid")
        else:
            app.extensions["agent_safety_state_store"] = store
            app.extensions["agent_safety_control_service"] = service
            app.extensions["agent_safety_recovery_service"] = recovery
            app.extensions["agent_safety_evaluation_service"] = evaluation
            containment_available = all(
                value is not None for value in (sandbox_control, egress_fence, credential_revocation)
            )
            status = AgentSafetyWiringStatus(True, containment_available, None)
    app.extensions["agent_safety_wiring_status"] = status
    return status


__all__ = ["AgentSafetyWiringStatus", "initialize_agent_safety"]
