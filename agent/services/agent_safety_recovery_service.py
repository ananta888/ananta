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
        stored = self._store.append(
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
        if disposition == "patched":
            self.create_regression_case(bundle_id=bundle_id)
        return stored

    def create_regression_case(self, *, bundle_id: str) -> dict[str, Any]:
        bundle = self._store.get("incident_bundle", require_token(bundle_id, "bundle_id"))
        disposition = self._store.get("incident_disposition", bundle_id)
        if not bundle or not disposition or disposition.get("disposition") != "patched":
            raise AgentSafetyDenied("agent_safety_patched_disposition_required")
        payload = {
            "regression_id": f"asreg_{bundle_id}",
            "bundle_id": bundle_id,
            "run_id": bundle["run_id"],
            "source_bundle_digest": bundle["bundle_digest"],
            "patch_digest": disposition["patch_digest"],
            "required_variants": ["exact", "mutated"],
            "replay_ids": [],
            "state": "pending",
            "created_at": utc_now(),
            "human_intervention_required": False,
        }
        existing = self._store.get("regression_case", payload["regression_id"])
        return existing or self._store.append("regression_case", payload["regression_id"], payload, expected_revision=0)

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
        stored = self._store.append("replay", replay_id, payload, expected_revision=0)
        regression = self._store.get("regression_case", f"asreg_{bundle_id}")
        if regression:
            replay_ids = sorted({*list(regression.get("replay_ids") or []), replay_id})
            self._store.append(
                "regression_case",
                str(regression["regression_id"]),
                {**regression, "replay_ids": replay_ids},
                expected_revision=int(regression["revision"]),
            )
        return stored

    def verify_fix(
        self,
        *,
        bundle_id: str,
        verification_id: str,
        results: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        regression = self._store.get("regression_case", f"asreg_{require_token(bundle_id, 'bundle_id')}")
        if not regression:
            raise AgentSafetyDenied("agent_safety_regression_case_required")
        variants = {str(result.get("variant") or "") for result in results}
        if not {"exact", "mutated"}.issubset(variants):
            raise AgentSafetyDenied("agent_safety_fix_verification_variants_required")
        replay_ids = set(regression.get("replay_ids") or [])
        passed = bool(results) and all(
            str(result.get("replay_id") or "") in replay_ids
            and bool(result.get("contained"))
            and bool(result.get("security_invariant_restored"))
            for result in results
        )
        payload = {
            "verification_id": require_token(verification_id, "verification_id"),
            "regression_id": regression["regression_id"],
            "bundle_id": bundle_id,
            "patch_digest": regression["patch_digest"],
            "result_digest": canonical_digest({"results": [dict(item) for item in results]}),
            "variants": sorted(variants),
            "state": "passed" if passed else "failed",
            "verified_at": utc_now(),
            "human_intervention_required": False,
        }
        stored = self._store.append("fix_verification", verification_id, payload, expected_revision=0)
        current = self._store.get("regression_case", str(regression["regression_id"])) or regression
        self._store.append(
            "regression_case",
            str(current["regression_id"]),
            {**current, "state": stored["state"], "last_verification_id": verification_id},
            expected_revision=int(current["revision"]),
        )
        if not passed:
            raise AgentSafetyDenied("agent_safety_fix_verification_failed")
        return stored


__all__ = ["AgentSafetyRecoveryService"]
