"""HMAC attestation for deterministic research evaluation receipts."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from ananta_contracts.research_training import canonical_json


class ResearchTrainingEvaluationAttestation:
    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            raise ValueError("research_evaluation_signing_key_too_short")
        self._key = bytes(signing_key)

    def issue(self, payload: Mapping[str, Any]) -> str:
        unsigned = {key: value for key, value in payload.items() if key != "attestation"}
        return hmac.new(self._key, canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()

    def verify(self, payload: Mapping[str, Any]) -> bool:
        provided = str(payload.get("attestation") or "")
        return bool(provided) and hmac.compare_digest(provided, self.issue(payload))


__all__ = ["ResearchTrainingEvaluationAttestation"]
