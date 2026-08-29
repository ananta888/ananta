"""Incident disposition and patch-before-retry recovery for agent safety."""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.agent_safety_errors import AgentSafetyDenied
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import canonical_digest, require_token, utc_now


class AgentSafetyRecoveryService:
    def __init__(self, store: AgentSafetyStateStorePort) -> None:
        self._store = store

    def classify_incident(
        self,
        *,
        bundle_id: str,
        causes: list[str],
        disposition: str,
        patch_digest: str | None,
    ) -> dict[str, Any]:
        allowed = {
            "model_policy_generalization",
            "prompt_task_specification",
            "sandbox_configuration",
            "runtime_vulnerability",
            "credential_access_control",
            "monitoring_gap",
        }
        if not causes or not set(causes).issubset(allowed):
            raise ValueError("agent_safety_root_cause_invalid")
        if disposition not in {"patched", "isolated_redteam_retry", "rejected"}:
            raise ValueError("agent_safety_disposition_invalid")
        if disposition == "patched" and not patch_digest:
            raise AgentSafetyDenied("agent_safety_patch_digest_required")
        bundle = self._store.get("incident_bundle", bundle_id)
        if not bundle:
            raise KeyError("agent_safety_incident_not_found")
        return self._store.append(
            "incident_disposition",
            bundle_id,
            {
                "bundle_id": bundle_id,
                "run_id": bundle["run_id"],
                "causes": sorted(set(causes)),
                "disposition": disposition,
                "patch_digest": patch_digest,
                "classified_at": utc_now(),
            },
            expected_revision=0,
        )

    def create_replay(
        self,
        *,
        bundle_id: str,
        replay_id: str,
        target_ref: str,
        mutation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle = self._store.get("incident_bundle", bundle_id)
        disposition = self._store.get("incident_disposition", bundle_id)
        if not bundle:
            raise KeyError("agent_safety_incident_not_found")
        if not target_ref.startswith("local:"):
            raise AgentSafetyDenied("agent_safety_replay_target_not_local")
        if not disposition or disposition["disposition"] not in {
            "patched",
            "isolated_redteam_retry",
        }:
            raise AgentSafetyDenied("agent_safety_patch_before_retry")
        payload = {
            "replay_id": require_token(replay_id, "replay_id"),
            "bundle_id": bundle_id,
            "run_id": bundle["run_id"],
            "target_ref": require_token(target_ref, "target_ref"),
            "patch_digest": disposition.get("patch_digest"),
            "mutation": dict(mutation or {}),
            "source_bundle_digest": bundle["bundle_digest"],
            "created_at": utc_now(),
        }
        payload["replay_digest"] = canonical_digest(payload)
        return self._store.append("replay", replay_id, payload, expected_revision=0)


__all__ = ["AgentSafetyRecoveryService"]
