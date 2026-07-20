"""Transport-neutral semantic-media capability contracts.

Capabilities are execution grants, not orchestration roles.  Only the Hub may
issue them and every grant is an attenuation of already-authorised rights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

CAPABILITY_CONTRACT_VERSION = 1

SemanticCapability = Literal[
    "capture",
    "publish",
    "subscribe",
    "compute",
    "validate",
    "evidence_transfer",
    "training_admission",
]
CapabilityDirection = Literal["ingress", "egress", "bidirectional", "none"]
CapabilityScopeKind = Literal["session", "room"]

SEMANTIC_CAPABILITIES: tuple[str, ...] = (
    "capture",
    "publish",
    "subscribe",
    "compute",
    "validate",
    "evidence_transfer",
    "training_admission",
)


@dataclass(frozen=True)
class SemanticMediaCapabilityGrant:
    version: int
    grant_id: str
    owner_id: str
    tenant_id: str
    subject_id: str
    subject_role: Literal["participant", "compute_executor", "lease_holder"]
    capability: SemanticCapability
    scope_kind: CapabilityScopeKind
    scope_id: str
    direction: CapabilityDirection
    data_type: str
    purpose: str
    epoch: int
    issued_at: float
    expires_at: float
    issuer: Literal["hub"] = "hub"
    signature: str = ""

    def unsigned_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("signature", None)
        return payload


__all__ = [
    "CAPABILITY_CONTRACT_VERSION",
    "SEMANTIC_CAPABILITIES",
    "SemanticMediaCapabilityGrant",
]
