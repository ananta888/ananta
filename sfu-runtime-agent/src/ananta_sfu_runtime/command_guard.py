"""Durable local replay/fencing guard for authenticated Hub commands.

This guard is not an orchestrator.  It only proves that a command from the Hub
is current, bounded and idempotent before invoking the narrow runtime backend.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


class RuntimeCommandGuardError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class RuntimeCommandGuardConfig:
    runtime_id: str
    config_digest: str
    capability_digest: str
    state_path: Path
    clock_skew_ms: int = 5_000
    command_lifetime_ms_max: int = 300_000
    receipt_count_max: int = 256
    state_bytes_max: int = 1_048_576

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeCommandGuardConfig":
        return cls(
            runtime_id=str(environment.get("ANANTA_SFU_RUNTIME_ID") or ""),
            config_digest=str(environment.get("ANANTA_SFU_RUNTIME_CONFIG_DIGEST") or ""),
            capability_digest=str(environment.get("ANANTA_SFU_RUNTIME_CAPABILITY_DIGEST") or ""),
            state_path=Path(str(environment.get("ANANTA_SFU_RUNTIME_COMMAND_STATE") or "")),
        )

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.runtime_id):
            raise RuntimeCommandGuardError("runtime_guard_runtime_id_invalid", status_code=503)
        for value in (self.config_digest, self.capability_digest):
            if not _DIGEST.fullmatch(value):
                raise RuntimeCommandGuardError("runtime_guard_digest_invalid", status_code=503)
        if not self.state_path.is_absolute():
            raise RuntimeCommandGuardError("runtime_guard_state_path_invalid", status_code=503)
        if not 0 <= self.clock_skew_ms <= 30_000:
            raise RuntimeCommandGuardError("runtime_guard_clock_skew_invalid", status_code=503)
        if not 1_000 <= self.command_lifetime_ms_max <= 600_000:
            raise RuntimeCommandGuardError("runtime_guard_lifetime_invalid", status_code=503)
        if not 16 <= self.receipt_count_max <= 2_048:
            raise RuntimeCommandGuardError("runtime_guard_receipt_bound_invalid", status_code=503)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PATH_COMMAND = {
    "/v1/routes/apply": "route_apply",
    "/v1/routes/update": "route_update",
    "/v1/routes/revoke": "route_revoke",
    "/v1/drain": "drain",
}
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "command_id",
        "command_type",
        "target_runtime_id",
        "nonce",
        "config_digest",
        "capability_digest",
        "flag_version",
        "cohort_version",
        "topology_epoch",
        "route_epoch",
        "parent_key_epoch",
        "fencing_token",
        "issued_at_ms",
        "expires_at_ms",
        "stale_access_deadline_ms",
        "payload_digest",
        "payload",
    }
)
_FORBIDDEN_POLICY_FIELDS = frozenset(
    {
        "task",
        "tasks",
        "worker",
        "workers",
        "membership",
        "consent",
        "audience",
        "layer_cap",
        "layer_caps",
        "ttl",
        "epoch",
        "fencing_token",
        "admission_policy",
        "placement_policy",
        "orchestrate",
    }
)


class RuntimeCommandGuard:
    def __init__(
        self,
        config: RuntimeCommandGuardConfig,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._config = config
        self._clock_ms = clock_ms
        self._process_lock = threading.Lock()

    def execute(
        self,
        *,
        path: str,
        envelope: Mapping[str, object],
        action: Callable[[Mapping[str, object]], Mapping[str, object]],
    ) -> Mapping[str, object]:
        command = self._validate(path, envelope)
        self._config.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self._config.state_path.with_suffix(self._config.state_path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with self._process_lock, os.fdopen(descriptor, "r+") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                state = self._load_state()
                duplicate = self._duplicate(state, command)
                if duplicate is not None:
                    return duplicate
                self._assert_current(state, command)
                # Persist the newer Hub authority before invoking infrastructure.
                # A backend failure must never let an older Hub fence become valid again.
                self._advance_state(state, command)
                self._persist_state(state)
                result = dict(action(command["payload"]))
                if self._clock_ms() >= int(command["effective_deadline_ms"]):
                    raise RuntimeCommandGuardError("runtime_command_deadline_exceeded")
                response = self._response(command, result)
                self._record(state, command, response)
                self._persist_state(state)
                return response
        finally:
            # fdopen owns and closes the descriptor on the successful path.
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _validate(self, path: str, envelope: Mapping[str, object]) -> dict[str, object]:
        if path not in _PATH_COMMAND:
            raise RuntimeCommandGuardError("runtime_command_path_invalid", status_code=404)
        if set(envelope) != _ENVELOPE_FIELDS:
            raise RuntimeCommandGuardError("runtime_command_envelope_invalid", status_code=400)
        command = dict(envelope)
        if command["schema_version"] != "sfu_runtime_control_command.v2":
            raise RuntimeCommandGuardError("runtime_command_schema_unsupported", status_code=400)
        for field in ("command_id", "target_runtime_id", "nonce"):
            if not _IDENTIFIER.fullmatch(str(command[field])):
                raise RuntimeCommandGuardError("runtime_command_identifier_invalid", status_code=400)
        if command["command_type"] != _PATH_COMMAND[path]:
            raise RuntimeCommandGuardError("runtime_command_type_mismatch", status_code=400)
        if command["target_runtime_id"] != self._config.runtime_id:
            raise RuntimeCommandGuardError("runtime_command_target_mismatch", status_code=403)
        if command["config_digest"] != self._config.config_digest:
            raise RuntimeCommandGuardError("runtime_command_config_mismatch", status_code=409)
        if command["capability_digest"] != self._config.capability_digest:
            raise RuntimeCommandGuardError("runtime_command_capability_mismatch", status_code=409)
        for field in ("config_digest", "capability_digest", "payload_digest"):
            if not _DIGEST.fullmatch(str(command[field])):
                raise RuntimeCommandGuardError("runtime_command_digest_invalid", status_code=400)
        for field, minimum in (
            ("flag_version", 0),
            ("cohort_version", 0),
            ("topology_epoch", 1),
            ("route_epoch", 1),
            ("parent_key_epoch", 1),
            ("fencing_token", 1),
            ("issued_at_ms", 1),
            ("expires_at_ms", 1),
            ("stale_access_deadline_ms", 1),
        ):
            value = command[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise RuntimeCommandGuardError("runtime_command_version_invalid", status_code=400)
        now = self._clock_ms()
        issued = int(command["issued_at_ms"])
        effective_deadline = min(
            int(command["expires_at_ms"]), int(command["stale_access_deadline_ms"])
        )
        if issued > now + self._config.clock_skew_ms:
            raise RuntimeCommandGuardError("runtime_command_clock_skew", status_code=400)
        if effective_deadline <= now:
            raise RuntimeCommandGuardError("runtime_command_expired", status_code=409)
        if effective_deadline - issued > self._config.command_lifetime_ms_max:
            raise RuntimeCommandGuardError("runtime_command_lifetime_exceeded", status_code=400)
        payload = command["payload"]
        if not isinstance(payload, Mapping) or self._contains_forbidden_field(payload):
            raise RuntimeCommandGuardError("runtime_command_payload_forbidden", status_code=400)
        canonical_payload = _canonical(payload)
        if len(canonical_payload) > 65_536:
            raise RuntimeCommandGuardError("runtime_command_payload_oversize", status_code=413)
        digest = "sha256:" + hashlib.sha256(canonical_payload).hexdigest()
        if digest != command["payload_digest"]:
            raise RuntimeCommandGuardError("runtime_command_payload_digest_mismatch", status_code=400)
        command["payload"] = dict(payload)
        command["effective_deadline_ms"] = effective_deadline
        command["route_scope"] = _route_scope(payload)
        return command

    def _assert_current(self, state: Mapping[str, object], command: Mapping[str, object]) -> None:
        if int(command["fencing_token"]) < int(state["fencing_token"]):
            raise RuntimeCommandGuardError("runtime_command_fencing_stale")
        for field in ("flag_version", "cohort_version"):
            if int(command[field]) < int(state[field]):
                raise RuntimeCommandGuardError("runtime_command_projection_stale")
        route = dict(state["routes"]).get(str(command["route_scope"]), {})
        for field in ("topology_epoch", "route_epoch", "parent_key_epoch"):
            if int(command[field]) < int(route.get(field, 0)):
                raise RuntimeCommandGuardError("runtime_command_epoch_stale")

    def _duplicate(
        self, state: Mapping[str, object], command: Mapping[str, object]
    ) -> Mapping[str, object] | None:
        for receipt in state["receipts"]:
            if receipt["command_id"] != command["command_id"]:
                continue
            if receipt["payload_digest"] != command["payload_digest"]:
                raise RuntimeCommandGuardError("runtime_command_idempotency_conflict")
            return dict(receipt["response"])
        return None

    def _record(
        self,
        state: dict[str, object],
        command: Mapping[str, object],
        response: Mapping[str, object],
    ) -> None:
        receipts = list(state["receipts"])
        receipts.append(
            {
                "command_id": command["command_id"],
                "payload_digest": command["payload_digest"],
                "response": dict(response),
            }
        )
        state["receipts"] = receipts[-self._config.receipt_count_max :]

    def _advance_state(
        self, state: dict[str, object], command: Mapping[str, object]
    ) -> None:
        state["fencing_token"] = int(command["fencing_token"])
        state["flag_version"] = int(command["flag_version"])
        state["cohort_version"] = int(command["cohort_version"])
        routes = dict(state["routes"])
        routes[str(command["route_scope"])] = {
            "topology_epoch": int(command["topology_epoch"]),
            "route_epoch": int(command["route_epoch"]),
            "parent_key_epoch": int(command["parent_key_epoch"]),
        }
        if len(routes) > 10_000:
            raise RuntimeCommandGuardError("runtime_command_route_state_exhausted", status_code=503)
        state["routes"] = routes

    def _response(
        self, command: Mapping[str, object], result: Mapping[str, object]
    ) -> dict[str, object]:
        response = dict(result)
        response.update(
            {
                "schema_version": "sfu_runtime_control_ack.v2",
                "command_id": command["command_id"],
                "target_runtime_id": command["target_runtime_id"],
                "nonce": command["nonce"],
                "config_digest": command["config_digest"],
                "capability_digest": command["capability_digest"],
                "flag_version": command["flag_version"],
                "cohort_version": command["cohort_version"],
                "topology_epoch": command["topology_epoch"],
                "route_epoch": command["route_epoch"],
                "parent_key_epoch": command["parent_key_epoch"],
                "fencing_token": command["fencing_token"],
            }
        )
        if len(_canonical(response)) > 65_536:
            raise RuntimeCommandGuardError("runtime_command_response_oversize", status_code=502)
        return response

    def _load_state(self) -> dict[str, object]:
        if not self._config.state_path.exists():
            return {
                "schema_version": "sfu_runtime_control_state.v1",
                "fencing_token": 0,
                "flag_version": 0,
                "cohort_version": 0,
                "routes": {},
                "receipts": [],
            }
        raw = self._config.state_path.read_bytes()
        if len(raw) > self._config.state_bytes_max:
            raise RuntimeCommandGuardError("runtime_command_state_oversize", status_code=503)
        try:
            state = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeCommandGuardError("runtime_command_state_invalid", status_code=503) from exc
        if set(state) != {
            "schema_version",
            "fencing_token",
            "flag_version",
            "cohort_version",
            "routes",
            "receipts",
        } or state["schema_version"] != "sfu_runtime_control_state.v1":
            raise RuntimeCommandGuardError("runtime_command_state_invalid", status_code=503)
        return state

    def _persist_state(self, state: Mapping[str, object]) -> None:
        payload = _canonical(state)
        if len(payload) > self._config.state_bytes_max:
            raise RuntimeCommandGuardError("runtime_command_state_oversize", status_code=503)
        temporary = self._config.state_path.with_name(
            f".{self._config.state_path.name}.{os.getpid()}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._config.state_path)
            directory = os.open(self._config.state_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    @classmethod
    def _contains_forbidden_field(cls, value: object) -> bool:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_POLICY_FIELDS:
                    return True
                if cls._contains_forbidden_field(child):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(cls._contains_forbidden_field(child) for child in value)
        return False


def _route_scope(payload: Mapping[str, object]) -> str:
    route = payload.get("route") if isinstance(payload.get("route"), Mapping) else payload
    route_id = str(route.get("route_id") or "")
    if not route_id:
        return "__runtime__"
    if not _IDENTIFIER.fullmatch(route_id):
        raise RuntimeCommandGuardError("runtime_command_route_id_invalid", status_code=400)
    return route_id


def _canonical(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise RuntimeCommandGuardError("runtime_command_json_invalid", status_code=400) from exc


__all__ = ["RuntimeCommandGuard", "RuntimeCommandGuardConfig", "RuntimeCommandGuardError"]
