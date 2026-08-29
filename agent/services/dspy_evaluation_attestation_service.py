"""Hub-local attestations binding evaluation results to promotion decisions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from ananta_contracts.dspy_optimization import canonical_json


class DspyEvaluationAttestationService:
    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            raise ValueError("dspy_evaluation_signing_key_too_short")
        self._key = bytes(signing_key)

    def issue(self, evaluation: Mapping[str, Any]) -> str:
        if "attestation" in evaluation:
            raise ValueError("dspy_evaluation_attestation_field_forbidden")
        return hmac.new(self._key, canonical_json(evaluation).encode(), hashlib.sha256).hexdigest()

    def verify(self, evaluation: Mapping[str, Any]) -> bool:
        value = dict(evaluation)
        supplied = value.pop("attestation", None)
        return isinstance(supplied, str) and hmac.compare_digest(self.issue(value), supplied)


__all__ = ["DspyEvaluationAttestationService"]
