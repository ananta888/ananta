"""Authenticated, monotonically fenced SFU runtime command boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Callable, Mapping, Protocol


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


class SfuBroadcastRuntimeControlPort(Protocol):
    def execute(self, command: SfuRuntimeControlCommand) -> SfuRuntimeControlResult: ...


class SfuRuntimeControlTransportPort(Protocol):
    def send(self, command: SfuRuntimeControlCommand) -> Mapping[str, object]: ...


class UnsupportedSfuRuntimeControlBoundary:
    """Fail-closed boundary whose methods retain compatible call signatures."""

    def execute(self, command: SfuRuntimeControlCommand, *args: object, **kwargs: object) -> SfuRuntimeControlResult:
        del args, kwargs
        return SfuRuntimeControlResult(
            accepted=False,
            authenticated=False,
            reason_code="sfu_runtime_control_unavailable",
            target_runtime_id=command.target_runtime_id,
            flag_version=command.flag_version,
            cohort_version=command.cohort_version,
            config_digest=command.config_digest,
            nonce=command.nonce,
            fencing_token=command.fencing_token,
        )

    def observe(self, target_runtime_id: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "available": False,
            "target_runtime_id": target_runtime_id,
            "reason_code": "sfu_runtime_observation_unavailable",
        }


class AuthenticatedSfuRuntimeControlBoundary:
    """Hub-side adapter; the transport never becomes an orchestration authority."""

    def __init__(
        self,
        transport: SfuRuntimeControlTransportPort,
        secret_resolver: Callable[[str], bytes | None],
    ) -> None:
        self._transport = transport
        self._secret_resolver = secret_resolver

    def execute(self, command: SfuRuntimeControlCommand) -> SfuRuntimeControlResult:
        rejection = _validate_command(command)
        if rejection is not None:
            return _rejected(command, rejection)
        try:
            response = dict(self._transport.send(command))
        except Exception:
            return _rejected(command, "sfu_runtime_control_transport_failed")
        secret = self._secret_resolver(command.target_runtime_id)
        signature = response.pop("signature", None)
        if not secret or not isinstance(signature, str):
            return _rejected(command, "sfu_runtime_ack_authentication_failed")
        try:
            canonical = json.dumps(
                response, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        except (TypeError, ValueError):
            return _rejected(command, "sfu_runtime_ack_invalid")
        expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return _rejected(command, "sfu_runtime_ack_authentication_failed")
        expected_fields = {
            "target_runtime_id": command.target_runtime_id,
            "flag_version": command.flag_version,
            "cohort_version": command.cohort_version,
            "config_digest": command.config_digest,
            "nonce": command.nonce,
            "fencing_token": command.fencing_token,
            "capability_digest": command.capability_digest,
            "topology_epoch": command.topology_epoch,
            "route_epoch": command.route_epoch,
            "parent_key_epoch": command.parent_key_epoch,
        }
        if any(response.get(key) != value for key, value in expected_fields.items()):
            return _rejected(command, "sfu_runtime_ack_conflict")
        accepted = response.get("accepted") is True
        return SfuRuntimeControlResult(
            accepted=accepted,
            authenticated=True,
            reason_code=str(response.get("reason_code") or ("accepted" if accepted else "sfu_runtime_rejected")),
            acknowledgement_digest=hashlib.sha256(canonical).hexdigest(),
            **expected_fields,
        )


def _rejected(command: SfuRuntimeControlCommand, reason_code: str) -> SfuRuntimeControlResult:
    fields = asdict(command)
    return SfuRuntimeControlResult(
        accepted=False,
        authenticated=False,
        reason_code=reason_code,
        target_runtime_id=str(fields["target_runtime_id"]),
        flag_version=int(fields["flag_version"]),
        cohort_version=int(fields["cohort_version"]),
        config_digest=str(fields["config_digest"]),
        nonce=str(fields["nonce"]),
        fencing_token=int(fields["fencing_token"]),
        capability_digest=str(fields["capability_digest"]),
        topology_epoch=int(fields["topology_epoch"]),
        route_epoch=int(fields["route_epoch"]),
        parent_key_epoch=int(fields["parent_key_epoch"]),
    )


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMAND_TYPES = frozenset({"route_apply", "route_update", "route_revoke", "drain"})


def _validate_command(command: SfuRuntimeControlCommand) -> str | None:
    if command.schema_version != "sfu_runtime_control_command.v2":
        return "sfu_runtime_command_schema_unsupported"
    if command.command_type not in _COMMAND_TYPES:
        return "sfu_runtime_command_type_unsupported"
    if not _DIGEST.fullmatch(command.config_digest) or not _DIGEST.fullmatch(
        command.capability_digest
    ):
        return "sfu_runtime_command_digest_invalid"
    for value in (
        command.topology_epoch,
        command.route_epoch,
        command.parent_key_epoch,
        command.fencing_token,
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return "sfu_runtime_command_fence_invalid"
    now = time.time()
    effective_deadline = min(command.deadline_at, command.stale_access_deadline_at)
    if command.issued_at > now + 5 or effective_deadline <= now:
        return "sfu_runtime_command_expired"
    if effective_deadline - command.issued_at > 300:
        return "sfu_runtime_command_lifetime_invalid"
    try:
        if len(json.dumps(command.wire_payload(), allow_nan=False)) > 65_536:
            return "sfu_runtime_command_oversize"
    except (TypeError, ValueError):
        return "sfu_runtime_command_payload_invalid"
    return None


__all__ = [
    "AuthenticatedSfuRuntimeControlBoundary",
    "SfuBroadcastRuntimeControlPort",
    "SfuRuntimeControlCommand",
    "SfuRuntimeControlResult",
    "SfuRuntimeControlTransportPort",
    "UnsupportedSfuRuntimeControlBoundary",
]
