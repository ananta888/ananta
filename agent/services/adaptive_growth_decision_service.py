"""Auditable Hub proposal boundary; scheduling stays in the existing task system."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from agent.services.adaptive_growth_policy import AdaptiveGrowthPolicy, GrowthSignals
from agent.services.decision_providers.service import DecisionRecordSink


class AdaptiveGrowthDecisionService:
    def __init__(self, *, policy: AdaptiveGrowthPolicy, audit: DecisionRecordSink):
        self._policy, self._audit = policy, audit

    def propose(self, signals: GrowthSignals, *, tenant_id: str, experiment_id: str) -> dict:
        if any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in (tenant_id, experiment_id)):
            return {"eligible": False, "reason": "scope_invalid", "action": "hold", "grants_permission": False}
        decision = self._policy.propose(signals)
        try:
            signal_digest = hashlib.sha256(
                json.dumps(asdict(signals), sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        except (ValueError, TypeError):
            signal_digest = None
        event = {
            "event": "adaptive_growth.proposal",
            "tenant_id": tenant_id,
            "experiment_id": experiment_id,
            "signal_digest": signal_digest,
            "policy_digest": hashlib.sha256(json.dumps(asdict(self._policy), sort_keys=True).encode()).hexdigest(),
            "decision": decision,
            "classification": "proposal",
            "production_release_evidence": False,
        }
        try:
            self._audit.record(event)
        except Exception:
            return {"eligible": False, "reason": "audit_unavailable", "action": "hold", "grants_permission": False}
        return decision
