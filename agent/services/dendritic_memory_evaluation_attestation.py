"""Hub-local integrity binding for benchmark decisions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from ananta_contracts.dendritic_memory import canonical_json


class DendriticMemoryEvaluationAttestation:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("dendritic_evaluation_key_too_short")
        self._key = bytes(key)

    def issue(self, value: Mapping[str, Any]) -> str:
        if "attestation" in value:
            raise ValueError("dendritic_evaluation_attestation_field_forbidden")
        return hmac.new(self._key, canonical_json(value).encode(), hashlib.sha256).hexdigest()

    def verify(self, value: Mapping[str, Any]) -> bool:
        payload = dict(value)
        supplied = payload.pop("attestation", None)
        return isinstance(supplied, str) and hmac.compare_digest(supplied, self.issue(payload))


__all__ = ["DendriticMemoryEvaluationAttestation"]
