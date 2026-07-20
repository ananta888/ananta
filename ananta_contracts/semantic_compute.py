"""Transport-neutral contracts for Hub-owned semantic compute.

The module deliberately contains no database, Flask, browser or worker-pool
dependency.  It validates the authority-bearing envelope at every process
boundary while leaving orchestration to the Hub.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

CAPABILITY_SCHEMA = "ananta.semantic-capability-advertisement.v1"
CONTRACT_SCHEMA = "ananta.semantic-compute-contract.v1"
LEASE_SCHEMA = "ananta.semantic-task-lease.v1"
WORKER_TASK_SCHEMA = "ananta.semantic-compute-task.v1"
WORKER_RESULT_SCHEMA = "ananta.semantic-compute-result.v1"

TASK_TYPES = frozenset({"visual_extract", "visual_validate", "speech_features", "speech_validate"})
OFFER_ROLES = frozenset({"executor", "validator", "standby"})
LEASE_ROLES = frozenset({"primary", "validator", "standby"})
SECURITY_MODES = frozenset({"strict_e2ee", "trusted_compute"})
PROFILES = frozenset({"off", "conservative", "balanced", "custom"})
KNOWN_ALGORITHMS = frozenset(
    {
        "heuristic-visual-v1",
        "speech-features-v1",
        "semantic-validator-v1",
        "ordinary-fallback-v1",
    }
)
MAX_CONTRACT_BYTES = 128 * 1024
MAX_WORKER_ARTIFACT_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


class SemanticComputeContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def canonical_json(value: Mapping[str, Any]) -> bytes:
    _finite_json(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticComputeContractError("invalid_json", "contract must be finite JSON") from exc
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise SemanticComputeContractError("payload_too_large", "contract payload exceeds its byte budget")
    return encoded


def contract_digest(value: Mapping[str, Any], *, omit: tuple[str, ...] = ("contract_digest", "signature")) -> str:
    payload = {key: item for key, item in value.items() if key not in omit}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def validate_capability_advertisement(raw: object, *, now_ms: int | None = None) -> dict[str, Any]:
    required = {
        "schema",
        "advertisement_id",
        "session_id",
        "epoch",
        "sender_id",
        "algorithms",
        "roles",
        "task_types",
        "resource_profile",
        "measurements_expires_at_ms",
        "expires_at_ms",
        "max_delay_ms",
        "max_artifact_bytes",
        "signature",
    }
    optional = {"room_id"}
    payload = _exact_mapping(raw, required=required, optional=optional, reason="invalid_capability")
    if payload["schema"] != CAPABILITY_SCHEMA:
        raise SemanticComputeContractError("invalid_schema", "unknown capability schema")
    for field in ("advertisement_id", "session_id", "sender_id"):
        _identifier(payload[field], field=field)
    if payload.get("room_id") is not None:
        _identifier(payload["room_id"], field="room_id")
    epoch = _integer(payload["epoch"], field="epoch", minimum=1, maximum=2_147_483_647)
    algorithms = _string_set(payload["algorithms"], field="algorithms", maximum=16)
    unknown_algorithms = sorted(set(algorithms) - KNOWN_ALGORITHMS)
    if unknown_algorithms:
        raise SemanticComputeContractError("unknown_capability", "advertisement contains an unknown algorithm")
    roles = _enum_set(payload["roles"], field="roles", allowed=OFFER_ROLES, maximum=4)
    task_types = _enum_set(payload["task_types"], field="task_types", allowed=TASK_TYPES, maximum=8)
    resources = _resource_profile(payload["resource_profile"])
    measurements_expiry = _integer(payload["measurements_expires_at_ms"], field="measurements_expires_at_ms", minimum=1)
    expiry = _integer(payload["expires_at_ms"], field="expires_at_ms", minimum=1)
    if expiry > measurements_expiry:
        raise SemanticComputeContractError("stale_measurement", "advertisement outlives its measurements")
    if now_ms is not None and expiry <= now_ms:
        raise SemanticComputeContractError("capability_expired", "capability advertisement expired")
    delay = _integer(payload["max_delay_ms"], field="max_delay_ms", minimum=2_000, maximum=20_000)
    artifact_bytes = _integer(
        payload["max_artifact_bytes"], field="max_artifact_bytes", minimum=1_024, maximum=MAX_WORKER_ARTIFACT_BYTES
    )
    signature = _signature(payload["signature"])
    return {
        "schema": CAPABILITY_SCHEMA,
        "advertisement_id": str(payload["advertisement_id"]),
        "session_id": str(payload["session_id"]),
        **({"room_id": str(payload["room_id"])} if payload.get("room_id") is not None else {}),
        "epoch": epoch,
        "sender_id": str(payload["sender_id"]),
        "algorithms": algorithms,
        "roles": roles,
        "task_types": task_types,
        "resource_profile": resources,
        "measurements_expires_at_ms": measurements_expiry,
        "expires_at_ms": expiry,
        "max_delay_ms": delay,
        "max_artifact_bytes": artifact_bytes,
        "signature": signature,
    }


def validate_quality_contract(
    raw: object,
    *,
    verify_digest: bool = True,
    now_ms: int | None = None,
) -> dict[str, Any]:
    required = {
        "schema",
        "contract_id",
        "session_id",
        "epoch",
        "revision",
        "issuer",
        "policy_version",
        "profile",
        "quality_level",
        "delay_ms",
        "security_mode",
        "trusted_compute_grant",
        "consent_version",
        "roles",
        "task_types",
        "max_artifact_bytes",
        "deadline_ms",
        "expires_at_ms",
        "contract_digest",
        "signature",
    }
    payload = _exact_mapping(raw, required=required, optional={"room_id"}, reason="invalid_contract")
    if payload["schema"] != CONTRACT_SCHEMA or payload["issuer"] != "hub":
        raise SemanticComputeContractError("invalid_authority", "compute contract is not Hub authoritative")
    for field in ("contract_id", "session_id", "policy_version"):
        _identifier(payload[field], field=field)
    if payload.get("room_id") is not None:
        _identifier(payload["room_id"], field="room_id")
    epoch = _integer(payload["epoch"], field="epoch", minimum=1)
    revision = _integer(payload["revision"], field="revision", minimum=1)
    profile = _enum(payload["profile"], field="profile", allowed=PROFILES)
    quality_level = _enum(
        payload["quality_level"],
        field="quality_level",
        allowed=frozenset({"best_effort", "standard", "verified"}),
    )
    delay_ms = _integer(payload["delay_ms"], field="delay_ms", minimum=2_000, maximum=20_000)
    security_mode = _enum(payload["security_mode"], field="security_mode", allowed=SECURITY_MODES)
    grant = _boolean(payload["trusted_compute_grant"], field="trusted_compute_grant")
    if security_mode == "strict_e2ee" and grant:
        raise SemanticComputeContractError("strict_e2ee_server_forbidden", "strict E2EE cannot grant server compute")
    consent_version = _integer(payload["consent_version"], field="consent_version", minimum=0)
    roles = _contract_roles(payload["roles"])
    task_types = _enum_set(payload["task_types"], field="task_types", allowed=TASK_TYPES, maximum=8)
    artifact_bytes = _integer(
        payload["max_artifact_bytes"], field="max_artifact_bytes", minimum=1_024, maximum=MAX_WORKER_ARTIFACT_BYTES
    )
    deadline_ms = _integer(payload["deadline_ms"], field="deadline_ms", minimum=100, maximum=20_000)
    expires_at_ms = _integer(payload["expires_at_ms"], field="expires_at_ms", minimum=1)
    if now_ms is not None and expires_at_ms <= now_ms:
        raise SemanticComputeContractError("contract_expired", "compute contract expired")
    digest = str(payload["contract_digest"])
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise SemanticComputeContractError("invalid_digest", "contract digest is invalid")
    normalized = {
        "schema": CONTRACT_SCHEMA,
        "contract_id": str(payload["contract_id"]),
        "session_id": str(payload["session_id"]),
        **({"room_id": str(payload["room_id"])} if payload.get("room_id") is not None else {}),
        "epoch": epoch,
        "revision": revision,
        "issuer": "hub",
        "policy_version": str(payload["policy_version"]),
        "profile": profile,
        "quality_level": quality_level,
        "delay_ms": delay_ms,
        "security_mode": security_mode,
        "trusted_compute_grant": grant,
        "consent_version": consent_version,
        "roles": roles,
        "task_types": task_types,
        "max_artifact_bytes": artifact_bytes,
        "deadline_ms": deadline_ms,
        "expires_at_ms": expires_at_ms,
        "contract_digest": digest,
        "signature": _signature(payload["signature"]),
    }
    if verify_digest and contract_digest(normalized) != digest:
        raise SemanticComputeContractError("digest_mismatch", "compute contract digest does not match")
    return normalized


def validate_task_lease(
    raw: object,
    *,
    now_ms: int | None = None,
    expected_audience: str | None = None,
) -> dict[str, Any]:
    """Validate cross-field lease invariants that JSON Schema cannot express."""

    required = {
        "schema",
        "lease_id",
        "contract_id",
        "contract_digest",
        "session_id",
        "epoch",
        "task_type",
        "role",
        "executor_id",
        "audience",
        "sequence_start",
        "sequence_end",
        "fencing_token",
        "resource_budget",
        "issued_at_ms",
        "expires_at_ms",
        "deadline_ms",
        "issuer",
        "signature",
    }
    payload = _exact_mapping(raw, required=required, optional={"room_id"}, reason="invalid_lease")
    if payload["schema"] != LEASE_SCHEMA or payload["issuer"] != "hub":
        raise SemanticComputeContractError("invalid_authority", "lease is not Hub authoritative")
    for field in ("lease_id", "contract_id", "session_id", "audience"):
        _identifier(payload[field], field=field)
    executor_id = _executor_identifier(payload["executor_id"])
    if payload.get("room_id") is not None:
        _identifier(payload["room_id"], field="room_id")
    start = _integer(payload["sequence_start"], field="sequence_start", minimum=0)
    end = _integer(payload["sequence_end"], field="sequence_end", minimum=0)
    if end < start or end - start > 10_000:
        raise SemanticComputeContractError("impossible_sequence_range", "lease sequence range is invalid")
    issued = _integer(payload["issued_at_ms"], field="issued_at_ms", minimum=1)
    expires = _integer(payload["expires_at_ms"], field="expires_at_ms", minimum=1)
    deadline = _integer(payload["deadline_ms"], field="deadline_ms", minimum=1, maximum=20_000)
    if expires <= issued or expires - issued > 300_000:
        raise SemanticComputeContractError("impossible_lease_window", "lease time window is invalid")
    if now_ms is not None and expires <= now_ms:
        raise SemanticComputeContractError("lease_expired", "lease expired")
    audience = str(payload["audience"])
    if expected_audience is not None and audience != expected_audience:
        raise SemanticComputeContractError("wrong_audience", "lease audience does not match")
    normalized = {
        "schema": LEASE_SCHEMA,
        "lease_id": str(payload["lease_id"]),
        "contract_id": str(payload["contract_id"]),
        "contract_digest": _digest(payload["contract_digest"], field="contract_digest"),
        "session_id": str(payload["session_id"]),
        **({"room_id": str(payload["room_id"])} if payload.get("room_id") is not None else {}),
        "epoch": _integer(payload["epoch"], field="epoch", minimum=1),
        "task_type": _enum(payload["task_type"], field="task_type", allowed=TASK_TYPES),
        "role": _enum(payload["role"], field="role", allowed=LEASE_ROLES),
        "executor_id": executor_id,
        "audience": audience,
        "sequence_start": start,
        "sequence_end": end,
        "fencing_token": _integer(payload["fencing_token"], field="fencing_token", minimum=1),
        "resource_budget": _resource_budget(payload["resource_budget"]),
        "issued_at_ms": issued,
        "expires_at_ms": expires,
        "deadline_ms": deadline,
        "issuer": "hub",
        "signature": _signature(payload["signature"]),
    }
    return normalized


@dataclass(frozen=True, slots=True)
class SemanticComputeWorkerTask:
    task_id: str
    parent_task_id: str
    contract_id: str
    contract_digest: str
    lease_id: str
    fencing_token: int
    session_id: str
    epoch: int
    task_type: str
    audience: str
    input_refs: tuple[str, ...]
    deadline_epoch_ms: int
    resource_budget: Mapping[str, int]
    artifact_publish_ref: str
    task_lease: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field in ("task_id", "parent_task_id", "contract_id", "lease_id", "session_id", "audience"):
            _identifier(getattr(self, field), field=field)
        if not re.fullmatch(r"[a-f0-9]{64}", self.contract_digest):
            raise SemanticComputeContractError("invalid_digest", "contract_digest is invalid")
        _integer(self.fencing_token, field="fencing_token", minimum=1)
        _integer(self.epoch, field="epoch", minimum=1)
        _enum(self.task_type, field="task_type", allowed=TASK_TYPES)
        _integer(self.deadline_epoch_ms, field="deadline_epoch_ms", minimum=1)
        if not 1 <= len(self.input_refs) <= 16 or len(set(self.input_refs)) != len(self.input_refs):
            raise SemanticComputeContractError("invalid_input_refs", "input references are invalid")
        if any(not re.fullmatch(r"artifact:[A-Za-z0-9_.:@-]{1,180}", item) for item in self.input_refs):
            raise SemanticComputeContractError("invalid_input_ref", "worker inputs must be opaque artifact references")
        if not re.fullmatch(r"artifact-publish:[A-Za-z0-9_.:@-]{1,170}", self.artifact_publish_ref):
            raise SemanticComputeContractError("invalid_publish_ref", "artifact publish reference is invalid")
        _resource_budget(self.resource_budget)
        if self.task_lease is not None:
            lease = validate_task_lease(self.task_lease, expected_audience=self.audience)
            expected = {
                "lease_id": self.lease_id,
                "contract_id": self.contract_id,
                "contract_digest": self.contract_digest,
                "session_id": self.session_id,
                "epoch": self.epoch,
                "task_type": self.task_type,
                "role": "primary",
                "audience": self.audience,
                "fencing_token": self.fencing_token,
            }
            if any(lease.get(field) != value for field, value in expected.items()):
                raise SemanticComputeContractError(
                    "task_lease_binding_mismatch",
                    "worker task and signed lease bindings differ",
                )
            lease_budget = dict(lease["resource_budget"])
            if any(
                int(self.resource_budget[field]) > int(lease_budget[field])
                for field in ("cpu_ms", "memory_bytes", "artifact_bytes")
            ):
                raise SemanticComputeContractError(
                    "task_lease_budget_exceeded",
                    "worker task budget exceeds its signed lease",
                )
            lease_deadline_ms = int(lease["issued_at_ms"]) + int(lease["deadline_ms"])
            if self.deadline_epoch_ms > min(
                int(lease["expires_at_ms"]),
                lease_deadline_ms,
            ):
                raise SemanticComputeContractError(
                    "task_lease_deadline_exceeded",
                    "worker task deadline exceeds its signed lease",
                )
            object.__setattr__(self, "task_lease", _copy_task_lease(lease))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKER_TASK_SCHEMA,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "contract_id": self.contract_id,
            "contract_digest": self.contract_digest,
            "lease_id": self.lease_id,
            "fencing_token": self.fencing_token,
            "session_id": self.session_id,
            "epoch": self.epoch,
            "task_type": self.task_type,
            "audience": self.audience,
            "input_refs": list(self.input_refs),
            "deadline_epoch_ms": self.deadline_epoch_ms,
            "resource_budget": dict(self.resource_budget),
            "artifact_publish_ref": self.artifact_publish_ref,
            **({"task_lease": _copy_task_lease(self.task_lease)} if self.task_lease is not None else {}),
            "execution_owner": "worker",
            "orchestration": {
                "owner": "hub",
                "may_create_tasks": False,
                "may_contact_peers": False,
                "may_contact_workers": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: object) -> "SemanticComputeWorkerTask":
        required = set(cls._field_names()) | {"schema", "execution_owner", "orchestration"}
        payload = _exact_mapping(
            raw,
            required=required,
            optional={"task_lease"},
            reason="invalid_worker_task",
        )
        if payload["schema"] != WORKER_TASK_SCHEMA or payload["execution_owner"] != "worker":
            raise SemanticComputeContractError("invalid_worker_task", "worker task authority is invalid")
        if payload["orchestration"] != {
            "owner": "hub",
            "may_create_tasks": False,
            "may_contact_peers": False,
            "may_contact_workers": False,
        }:
            raise SemanticComputeContractError("worker_orchestration_forbidden", "worker may not orchestrate")
        if "task_lease" in payload and not isinstance(payload["task_lease"], Mapping):
            raise SemanticComputeContractError(
                "invalid_worker_task",
                "task_lease must be an object",
            )
        return cls(
            task_id=str(payload["task_id"]),
            parent_task_id=str(payload["parent_task_id"]),
            contract_id=str(payload["contract_id"]),
            contract_digest=str(payload["contract_digest"]),
            lease_id=str(payload["lease_id"]),
            fencing_token=_strict_int(payload["fencing_token"], "fencing_token"),
            session_id=str(payload["session_id"]),
            epoch=_strict_int(payload["epoch"], "epoch"),
            task_type=str(payload["task_type"]),
            audience=str(payload["audience"]),
            input_refs=tuple(_strict_strings(payload["input_refs"], "input_refs")),
            deadline_epoch_ms=_strict_int(payload["deadline_epoch_ms"], "deadline_epoch_ms"),
            resource_budget=_strict_integer_mapping(payload["resource_budget"], "resource_budget"),
            artifact_publish_ref=str(payload["artifact_publish_ref"]),
            task_lease=payload.get("task_lease"),
        )

    @staticmethod
    def _field_names() -> tuple[str, ...]:
        return (
            "task_id",
            "parent_task_id",
            "contract_id",
            "contract_digest",
            "lease_id",
            "fencing_token",
            "session_id",
            "epoch",
            "task_type",
            "audience",
            "input_refs",
            "deadline_epoch_ms",
            "resource_budget",
            "artifact_publish_ref",
        )


@dataclass(frozen=True, slots=True)
class SemanticComputeWorkerResult:
    task_id: str
    contract_id: str
    contract_digest: str
    lease_id: str
    fencing_token: int
    session_id: str
    epoch: int
    task_type: str
    audience: str
    status: str
    result_digest: str
    artifact_refs: tuple[str, ...]
    completed_at_ms: int
    reason_code: str | None = None
    metrics: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        for field in ("task_id", "contract_id", "lease_id", "session_id", "audience"):
            _identifier(getattr(self, field), field=field)
        _digest(self.contract_digest, field="contract_digest")
        _digest(self.result_digest, field="result_digest")
        _integer(self.fencing_token, field="fencing_token", minimum=1)
        _integer(self.epoch, field="epoch", minimum=1)
        _enum(self.task_type, field="task_type", allowed=TASK_TYPES)
        _enum(self.status, field="status", allowed=frozenset({"completed", "failed", "cancelled"}))
        _integer(self.completed_at_ms, field="completed_at_ms", minimum=1)
        if len(self.artifact_refs) > 8 or len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise SemanticComputeContractError("invalid_artifact_refs", "result artifact references are invalid")
        if any(not re.fullmatch(r"artifact:[A-Za-z0-9_.:@-]{1,180}", item) for item in self.artifact_refs):
            raise SemanticComputeContractError("invalid_artifact_ref", "result contains an invalid artifact reference")
        if self.reason_code is not None and not _REASON.fullmatch(self.reason_code):
            raise SemanticComputeContractError("invalid_reason_code", "worker reason code is invalid")
        _finite_metrics(self.metrics)

    @classmethod
    def from_dict(cls, raw: object) -> "SemanticComputeWorkerResult":
        required = {
            "schema",
            "task_id",
            "contract_id",
            "contract_digest",
            "lease_id",
            "fencing_token",
            "session_id",
            "epoch",
            "task_type",
            "audience",
            "status",
            "result_digest",
            "artifact_refs",
            "completed_at_ms",
            "execution_owner",
        }
        payload = _exact_mapping(
            raw,
            required=required,
            optional={"reason_code", "metrics"},
            reason="invalid_worker_result",
        )
        if payload["schema"] != WORKER_RESULT_SCHEMA or payload["execution_owner"] != "worker":
            raise SemanticComputeContractError("invalid_worker_result", "worker result authority is invalid")
        result = cls(
            task_id=_identifier(payload["task_id"], field="task_id"),
            contract_id=_identifier(payload["contract_id"], field="contract_id"),
            contract_digest=_digest(payload["contract_digest"], field="contract_digest"),
            lease_id=_identifier(payload["lease_id"], field="lease_id"),
            fencing_token=_integer(payload["fencing_token"], field="fencing_token", minimum=1),
            session_id=_identifier(payload["session_id"], field="session_id"),
            epoch=_integer(payload["epoch"], field="epoch", minimum=1),
            task_type=_enum(payload["task_type"], field="task_type", allowed=TASK_TYPES),
            audience=_identifier(payload["audience"], field="audience"),
            status=_enum(payload["status"], field="status", allowed=frozenset({"completed", "failed", "cancelled"})),
            result_digest=_digest(payload["result_digest"], field="result_digest"),
            artifact_refs=tuple(_strict_strings(payload["artifact_refs"], "artifact_refs")),
            completed_at_ms=_integer(payload["completed_at_ms"], field="completed_at_ms", minimum=1),
            reason_code=str(payload["reason_code"]) if payload.get("reason_code") is not None else None,
            metrics=_finite_metrics(payload.get("metrics")),
        )
        if len(result.artifact_refs) > 8 or len(set(result.artifact_refs)) != len(result.artifact_refs):
            raise SemanticComputeContractError("invalid_artifact_refs", "result artifact references are invalid")
        if any(not re.fullmatch(r"artifact:[A-Za-z0-9_.:@-]{1,180}", item) for item in result.artifact_refs):
            raise SemanticComputeContractError("invalid_artifact_ref", "result contains an invalid artifact reference")
        if result.reason_code is not None and not _REASON.fullmatch(result.reason_code):
            raise SemanticComputeContractError("invalid_reason_code", "worker reason code is invalid")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKER_RESULT_SCHEMA,
            "task_id": self.task_id,
            "contract_id": self.contract_id,
            "contract_digest": self.contract_digest,
            "lease_id": self.lease_id,
            "fencing_token": self.fencing_token,
            "session_id": self.session_id,
            "epoch": self.epoch,
            "task_type": self.task_type,
            "audience": self.audience,
            "status": self.status,
            "reason_code": self.reason_code,
            "result_digest": self.result_digest,
            "artifact_refs": list(self.artifact_refs),
            "metrics": dict(self.metrics or {}),
            "completed_at_ms": self.completed_at_ms,
            "execution_owner": "worker",
        }


def _finite_json(value: object, *, depth: int = 0) -> None:
    if depth > 12:
        raise SemanticComputeContractError("payload_too_deep", "contract nesting is too deep")
    if isinstance(value, float) and not math.isfinite(value):
        raise SemanticComputeContractError("non_finite_value", "contract contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SemanticComputeContractError("invalid_json", "contract keys must be strings")
            _finite_json(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_json(item, depth=depth + 1)


def _exact_mapping(raw: object, *, required: set[str], optional: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) - required - optional or required - set(raw):
        raise SemanticComputeContractError(reason, "contract fields are incomplete or unknown")
    payload = dict(raw)
    canonical_json(payload)
    return payload


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise SemanticComputeContractError("invalid_identifier", f"{field} is invalid")
    return value


def _executor_identifier(value: object) -> str:
    if isinstance(value, str) and _ID.fullmatch(value) is not None:
        return value
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value.encode("utf-8")) <= 512
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise SemanticComputeContractError("invalid_executor_id", "executor_id is invalid")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise SemanticComputeContractError("invalid_executor_id", "executor_id is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SemanticComputeContractError("invalid_executor_id", "executor_id is invalid")
    return value


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise SemanticComputeContractError("invalid_digest", f"{field} is invalid")
    return value


def _strict_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SemanticComputeContractError("invalid_integer", f"{field} must be an integer")
    return value


def _integer(value: object, *, field: str, minimum: int, maximum: int = 9_007_199_254_740_991) -> int:
    normalized = _strict_int(value, field)
    if not minimum <= normalized <= maximum:
        raise SemanticComputeContractError("impossible_budget", f"{field} is outside its allowed range")
    return normalized


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticComputeContractError("invalid_boolean", f"{field} must be a boolean")
    return value


def _enum(value: object, *, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SemanticComputeContractError("invalid_enum", f"{field} is invalid")
    return value


def _strict_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SemanticComputeContractError("invalid_array", f"{field} must be a string array")
    return list(value)


def _string_set(value: object, *, field: str, maximum: int) -> list[str]:
    items = _strict_strings(value, field)
    if not 1 <= len(items) <= maximum or len(set(items)) != len(items):
        raise SemanticComputeContractError("invalid_array", f"{field} has invalid cardinality")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", item) for item in items):
        raise SemanticComputeContractError("invalid_identifier", f"{field} contains an invalid value")
    return sorted(items)


def _enum_set(value: object, *, field: str, allowed: frozenset[str], maximum: int) -> list[str]:
    items = _strict_strings(value, field)
    if not 1 <= len(items) <= maximum or len(set(items)) != len(items) or set(items) - allowed:
        raise SemanticComputeContractError("invalid_enum", f"{field} contains an unsupported value")
    return sorted(items)


def _resource_profile(value: object) -> dict[str, str]:
    required = {"cpu", "memory", "gpu", "codec", "battery", "network"}
    payload = _exact_mapping(value, required=required, optional=set(), reason="invalid_resource_profile")
    allowed = {
        "cpu": frozenset({"unknown", "low", "medium", "high"}),
        "memory": frozenset({"unknown", "low", "medium", "high"}),
        "gpu": frozenset({"unknown", "none", "integrated", "dedicated"}),
        "codec": frozenset({"unknown", "software", "hardware"}),
        "battery": frozenset({"unknown", "critical", "limited", "mains"}),
        "network": frozenset({"unknown", "constrained", "normal", "fast"}),
    }
    return {field: _enum(payload[field], field=field, allowed=values) for field, values in allowed.items()}


def _signature(value: object) -> dict[str, str]:
    payload = _exact_mapping(
        value,
        required={"algorithm", "key_id", "value"},
        optional=set(),
        reason="invalid_signature",
    )
    algorithm = _enum(payload["algorithm"], field="algorithm", allowed=frozenset({"ed25519", "hmac-sha256"}))
    key_id = _identifier(payload["key_id"], field="key_id")
    signature_value = str(payload["value"])
    if not 16 <= len(signature_value) <= 512 or "\x00" in signature_value:
        raise SemanticComputeContractError("invalid_signature", "signature value is invalid")
    return {"algorithm": algorithm, "key_id": key_id, "value": signature_value}


def _copy_task_lease(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached closed lease mapping for immutable task envelopes."""

    copied = dict(value)
    copied["resource_budget"] = dict(value.get("resource_budget") or {})
    copied["signature"] = dict(value.get("signature") or {})
    return copied


def _contract_roles(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) - {"primary", "validator", "standby"}:
        raise SemanticComputeContractError("invalid_roles", "contract roles are invalid")
    result: dict[str, list[str]] = {}
    for role, members in value.items():
        values = _strict_strings(members, f"roles.{role}")
        maximum = 2 if role == "validator" else 1
        if len(values) > maximum or len(set(values)) != len(values):
            raise SemanticComputeContractError("invalid_roles", "contract role cardinality is invalid")
        result[str(role)] = sorted(_identifier(item, field=f"roles.{role}") for item in values)
    return result


def _strict_integer_mapping(value: object, field: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SemanticComputeContractError("invalid_object", f"{field} must be an object")
    return {str(key): _strict_int(item, f"{field}.{key}") for key, item in value.items()}


def _resource_budget(value: object) -> dict[str, int]:
    payload = _strict_integer_mapping(value, "resource_budget")
    if set(payload) != {"cpu_ms", "memory_bytes", "artifact_bytes"}:
        raise SemanticComputeContractError("invalid_resource_budget", "resource budget fields are invalid")
    _integer(payload["cpu_ms"], field="cpu_ms", minimum=1, maximum=20_000)
    _integer(payload["memory_bytes"], field="memory_bytes", minimum=1_048_576, maximum=1_073_741_824)
    _integer(payload["artifact_bytes"], field="artifact_bytes", minimum=1, maximum=MAX_WORKER_ARTIFACT_BYTES)
    return payload


def _finite_metrics(value: object) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or len(value) > 8:
        raise SemanticComputeContractError("invalid_metrics", "worker metrics are invalid")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise SemanticComputeContractError("invalid_metrics", "worker metrics are invalid")
        number = float(raw)
        if not math.isfinite(number) or not 0 <= number <= 1_000_000_000:
            raise SemanticComputeContractError("invalid_metrics", "worker metrics are invalid")
        result[key] = number
    return result


__all__ = [
    "CAPABILITY_SCHEMA",
    "CONTRACT_SCHEMA",
    "LEASE_SCHEMA",
    "WORKER_RESULT_SCHEMA",
    "WORKER_TASK_SCHEMA",
    "KNOWN_ALGORITHMS",
    "MAX_WORKER_ARTIFACT_BYTES",
    "PROFILES",
    "SECURITY_MODES",
    "TASK_TYPES",
    "SemanticComputeContractError",
    "SemanticComputeWorkerResult",
    "SemanticComputeWorkerTask",
    "canonical_json",
    "contract_digest",
    "validate_capability_advertisement",
    "validate_quality_contract",
    "validate_task_lease",
]
