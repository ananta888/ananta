"""Wire value types of the authenticated SFU runtime command boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SfuRuntimeControlCommand:
    command_id: str
    command_type: str
    target_runtime_id: str
    tenant_id: str
    flag_version: int
    cohort_version: int
    config_digest: str
    nonce: str
    fencing_token: int
    issued_at: float
    deadline_at: float
    payload: Mapping[str, object]
    schema_version: str = "sfu_runtime_control_command.v2"
    capability_digest: str = ""
    topology_epoch: int = 0
    route_epoch: int = 0
    parent_key_epoch: int = 0
    stale_access_deadline_at: float = 0.0

    def wire_payload(self) -> Mapping[str, object]:
        payload_bytes = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "command_type": self.command_type,
            "target_runtime_id": self.target_runtime_id,
            "nonce": self.nonce,
            "config_digest": self.config_digest,
            "capability_digest": self.capability_digest,
            "flag_version": self.flag_version,
            "cohort_version": self.cohort_version,
            "topology_epoch": self.topology_epoch,
            "route_epoch": self.route_epoch,
            "parent_key_epoch": self.parent_key_epoch,
            "fencing_token": self.fencing_token,
            "issued_at_ms": int(self.issued_at * 1_000),
            "expires_at_ms": int(self.deadline_at * 1_000),
            "stale_access_deadline_ms": int(self.stale_access_deadline_at * 1_000),
            "payload_digest": "sha256:" + hashlib.sha256(payload_bytes).hexdigest(),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SfuRuntimeControlResult:
    accepted: bool
    authenticated: bool
    reason_code: str
    target_runtime_id: str
    flag_version: int
    cohort_version: int
    config_digest: str
    nonce: str
    fencing_token: int
    acknowledgement_digest: str | None = None
    capability_digest: str = ""
    topology_epoch: int = 0
    route_epoch: int = 0
    parent_key_epoch: int = 0


__all__ = [
    "SfuRuntimeControlCommand",
    "SfuRuntimeControlResult",
]
