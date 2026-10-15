"""Adapt an existing Specialist contract to an immutable inference-only baseline."""

from __future__ import annotations

import json

from agent.services.decision_providers.base import DecisionProvider
from agent.services.decision_providers.types import DecisionQuestion, DecisionRequest
from ananta_contracts.specialist_decision import OUTPUT_SCHEMA, SpecialistDecisionContract


class SpecialistDecisionBaseline:
    def __init__(self, provider: DecisionProvider, contract: SpecialistDecisionContract):
        self._provider, self._contract = provider, contract

    @property
    def descriptor(self):
        return {
            "provider": self._provider.provider_id,
            "contract_digest": self._contract.digest,
            "role": "frozen_inference_baseline",
            "training": "unsupported",
            "export_gguf": "unsupported",
            "grants_permission": False,
        }

    def predict(
        self,
        inputs,
        *,
        tenant_id: str,
        subject_id: str,
        timeout_seconds: float = 5,
        assignment_token: str | None = None,
    ):
        if not tenant_id or not subject_id:
            raise ValueError("specialist_baseline_scope_required")
        normalized = self._contract.validate_input(inputs)
        question = DecisionQuestion.choice(
            "decision", self._contract.decision, {label: label for label in self._contract.allowed_labels}
        )
        result = self._provider.decide(
            DecisionRequest(
                json.dumps(normalized, ensure_ascii=False),
                (question,),
                purpose=self._contract.specialist_id,
                tenant_id=tenant_id,
                subject_id=subject_id,
                assignment_token=assignment_token,
            ),
            timeout_seconds=timeout_seconds,
        )
        answer = result.answers["decision"]
        output = {"schema": OUTPUT_SCHEMA, "label": answer.choice}
        if self._contract.confidence.enabled:
            output["confidence"] = answer.confidence
        return self._contract.apply_abstention(self._contract.parse_output(output))
