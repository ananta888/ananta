"""Attested automatic runtime decision bound to one evaluated pack."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from agent.services.dendritic_memory_evaluation_attestation import DendriticMemoryEvaluationAttestation
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from ananta_contracts.dendritic_memory import canonical_digest, canonical_json, require_digest


class DendriticMemoryRuntimeGate:
    def __init__(
        self,
        *,
        policy: DendriticMemoryPolicy,
        evaluations: DendriticMemoryEvaluationAttestation,
        signing_key: bytes,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("dendritic_runtime_gate_key_too_short")
        self._policy = policy
        self._evaluations = evaluations
        self._key = bytes(signing_key)

    def evaluate(
        self,
        *,
        pack_digest: str,
        base_model_snapshot_digest: str,
        evaluation: Mapping[str, Any],
        capability: Mapping[str, Any],
    ) -> dict[str, Any]:
        pack = require_digest(pack_digest, "pack_digest")
        base = require_digest(base_model_snapshot_digest, "base_model_snapshot_digest")
        reasons: list[str] = []
        if not self._policy.runtime_enabled or not self._policy.automatic_activation_enabled:
            reasons.append("dendritic_runtime_disabled")
        if not self._evaluations.verify(evaluation):
            reasons.append("dendritic_evaluation_attestation_invalid")
        elif not evaluation.get("experiment_eligible") or evaluation.get("dendritic_pack_digest") != pack:
            reasons.append("dendritic_evaluation_pack_gate_failed")
        if capability.get("state") != "available" or capability.get("available") is not True:
            reasons.append("dendritic_runtime_capability_unavailable")
        result = {
            "schema": "ananta.dendritic-memory-runtime-gate.v1",
            "eligible": not reasons,
            "reason_codes": reasons,
            "pack_digest": pack,
            "base_model_snapshot_digest": base,
            "evaluation_digest": evaluation.get("evaluation_digest"),
            "capability_digest": canonical_digest(capability),
            "policy_digest": self._policy.digest,
            "human_intervention_required": False,
        }
        result["receipt_digest"] = canonical_digest(result)
        result["attestation"] = hmac.new(
            self._key, canonical_json(result).encode(), hashlib.sha256
        ).hexdigest()
        return result

    def verify(self, receipt: Mapping[str, Any]) -> bool:
        value = dict(receipt)
        supplied = value.pop("attestation", None)
        expected = hmac.new(self._key, canonical_json(value).encode(), hashlib.sha256).hexdigest()
        return isinstance(supplied, str) and hmac.compare_digest(supplied, expected)


__all__ = ["DendriticMemoryRuntimeGate"]
