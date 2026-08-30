"""Hub-owned automatic preauthorization policy for agent-safety modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.agent_safety_errors import AgentSafetyDenied
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import SafetyMode, canonical_digest, require_token, utc_now


@dataclass(frozen=True, slots=True)
class AgentSafetyPreauthorizationConfig:
    policy_id: str = "hub-agent-safety-default-v1"
    allowed_modes: tuple[str, ...] = (
        SafetyMode.ENFORCE.value,
        SafetyMode.OBSERVE_ONLY.value,
        SafetyMode.ADVERSARIAL_EVAL.value,
        SafetyMode.DISABLED.value,
    )
    max_parallel_agents: int = 100
    require_local_adversarial_targets: bool = True


class AgentSafetyAdmissionPolicy:
    def __init__(
        self,
        store: AgentSafetyStateStorePort,
        *,
        config: AgentSafetyPreauthorizationConfig | None = None,
    ) -> None:
        self._store = store
        self._config = config or AgentSafetyPreauthorizationConfig()

    def authorize_configuration(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        policy_id = require_token(payload.get("policy_id"), "policy_id")
        revision = int(payload.get("revision") or 1)
        mode = SafetyMode(str(payload.get("mode") or SafetyMode.ENFORCE))
        targets = tuple(str(item) for item in payload.get("adversarial_scope") or ())
        reasons: list[str] = []
        if not bool(payload.get("automatic_authorization", True)):
            reasons.append("agent_safety_automatic_authorization_required")
        if mode.value not in set(self._config.allowed_modes):
            reasons.append("agent_safety_mode_not_pre_authorized")
        if int(payload.get("max_parallel_agents") or 1) > self._config.max_parallel_agents:
            reasons.append("agent_safety_parallelism_not_pre_authorized")
        if mode == SafetyMode.ADVERSARIAL_EVAL and self._config.require_local_adversarial_targets:
            if not targets or any(not target.startswith("local:") for target in targets):
                reasons.append("agent_safety_adversarial_target_not_pre_authorized")
        if not bool(payload.get("telemetry_enabled", True)) or not bool(
            payload.get("external_kill_switch_enabled", True)
        ):
            reasons.append("agent_safety_mandatory_controls_not_pre_authorized")
        decision = {
            "authorization_id": f"{policy_id}:{revision}",
            "authorization_policy_id": self._config.policy_id,
            "policy_id": policy_id,
            "policy_revision": revision,
            "decision": "allow" if not reasons else "deny",
            "reason_codes": reasons or ["agent_safety_pre_authorized"],
            "request_digest": canonical_digest(dict(payload)),
            "decided_at": utc_now(),
            "human_intervention_required": False,
        }
        existing = self._store.get("policy_authorization", decision["authorization_id"])
        if existing:
            if existing.get("request_digest") != decision["request_digest"]:
                raise AgentSafetyDenied("agent_safety_authorization_revision_conflict")
            decision = existing
        else:
            decision = self._store.append(
                "policy_authorization",
                decision["authorization_id"],
                decision,
                expected_revision=0,
            )
        if reasons:
            raise AgentSafetyDenied(reasons[0])
        return decision


__all__ = ["AgentSafetyAdmissionPolicy", "AgentSafetyPreauthorizationConfig"]
