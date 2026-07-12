"""Hub-safe wire contract for restricted, non-generative model inference.

This module deliberately has no dependency on model adapters or optional ML
libraries.  The hub may validate and serialize work here, while a worker owns
the actual model runtime.  Keeping the contract independent is the primary
dependency-inversion seam for moving inference out of the control plane.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, cast

CONTRACT_VERSION = "restricted_inference.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$")
_REASON_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "code",
        "completion",
        "content",
        "generated",
        "generated_text",
        "generation",
        "message",
        "tool_call",
        "tool_calls",
    }
)


class RestrictedInferenceOperation(str, Enum):
    EMBED = "embed"
    CLASSIFY = "classify"
    RERANK = "rerank"
    SCORE_CHOICES = "score_choices"
    EXTRACT_FEATURES = "extract_features"
    RISK_SCORE = "risk_score"


class RestrictedInferenceStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RestrictedInferenceContractError(ValueError):
    """Raised when an inference envelope violates the wire contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_REQUEST_REQUIRED_KEYS: dict[RestrictedInferenceOperation, frozenset[str]] = {
    RestrictedInferenceOperation.EMBED: frozenset({"texts"}),
    RestrictedInferenceOperation.CLASSIFY: frozenset({"text", "labels"}),
    RestrictedInferenceOperation.RERANK: frozenset({"query", "candidates"}),
    RestrictedInferenceOperation.SCORE_CHOICES: frozenset({"prompt", "choices"}),
    RestrictedInferenceOperation.EXTRACT_FEATURES: frozenset({"text"}),
    RestrictedInferenceOperation.RISK_SCORE: frozenset({"input"}),
}

_RESULT_ALLOWED_KEYS: dict[RestrictedInferenceOperation, frozenset[str]] = {
    RestrictedInferenceOperation.EMBED: frozenset({"vectors"}),
    RestrictedInferenceOperation.CLASSIFY: frozenset({"label", "confidence", "all_scores"}),
    RestrictedInferenceOperation.RERANK: frozenset({"items"}),
    RestrictedInferenceOperation.SCORE_CHOICES: frozenset({"items"}),
    RestrictedInferenceOperation.EXTRACT_FEATURES: frozenset({"vector", "dimensions"}),
    RestrictedInferenceOperation.RISK_SCORE: frozenset({"risk_score", "risk_category", "confidence"}),
}
_RESULT_METADATA_KEYS = frozenset({"engine", "latency_ms", "manifest_digest", "model_id"})
_REQUEST_REQUIRED_WIRE_FIELDS = frozenset(
    {
        "contract_version",
        "deadline_epoch_ms",
        "idempotency_key",
        "model_manifest_id",
        "operation",
        "paths",
        "payload",
        "policy_hash",
        "request_id",
        "task_id",
        "tenant_id",
    }
)
_REQUEST_OPTIONAL_WIRE_FIELDS = frozenset({"execution_policy", "run_id"})
_REQUEST_WIRE_FIELDS = _REQUEST_REQUIRED_WIRE_FIELDS | _REQUEST_OPTIONAL_WIRE_FIELDS
_RESPONSE_WIRE_FIELDS = frozenset(
    {
        "contract_version",
        "error",
        "no_generation",
        "operation",
        "request_id",
        "result",
        "status",
        "task_id",
    }
)
_ERROR_WIRE_FIELDS = frozenset({"code", "message", "retryable"})
_EXECUTION_POLICY_FIELDS = frozenset(
    {
        "allow_attention",
        "allow_cpu_fallback",
        "allow_hidden_states",
        "device",
        "max_batch_size",
        "max_candidates",
        "max_input_chars",
        "max_output_dimensions",
    }
)
_DEFAULT_EXECUTION_POLICY: dict[str, Any] = {
    "allow_attention": False,
    "allow_cpu_fallback": False,
    "allow_hidden_states": False,
    "device": "",
    "max_batch_size": 64,
    "max_candidates": 64,
    "max_input_chars": 1_000_000,
    "max_output_dimensions": 65_536,
}


def _require_identifier(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise RestrictedInferenceContractError(
            "invalid_identifier",
            f"{name} must be a non-empty, bounded identifier",
        )
    return normalized


def _json_copy(value: Any, *, reason_code: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise RestrictedInferenceContractError(reason_code, "value must be finite JSON data") from exc


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _deep_freeze(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(nested) for nested in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(nested) for nested in value]
    return value


def _string_list(payload: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> list[str]:
    raw = payload.get(key)
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise RestrictedInferenceContractError("invalid_payload", f"{key} must be a non-empty list")
    if any(not isinstance(item, str) for item in raw):
        raise RestrictedInferenceContractError("invalid_payload", f"{key} entries must be strings")
    values = [str(item) for item in raw]
    if any(not item for item in values):
        raise RestrictedInferenceContractError("invalid_payload", f"{key} entries must be non-empty strings")
    return values


def _validate_request_payload(operation: RestrictedInferenceOperation, payload: Mapping[str, Any]) -> None:
    required = _REQUEST_REQUIRED_KEYS[operation]
    keys = frozenset(payload)
    if keys != required:
        missing = sorted(required - keys)
        unknown = sorted(keys - required)
        raise RestrictedInferenceContractError(
            "invalid_payload_shape",
            f"payload fields do not match {operation.value}: missing={missing}, unknown={unknown}",
        )

    if operation is RestrictedInferenceOperation.EMBED:
        _string_list(payload, "texts")
    elif operation is RestrictedInferenceOperation.CLASSIFY:
        if not isinstance(payload.get("text"), str):
            raise RestrictedInferenceContractError("invalid_payload", "text must be a string")
        labels = _string_list(payload, "labels")
        if len(set(labels)) != len(labels):
            raise RestrictedInferenceContractError("invalid_payload", "labels must be unique")
    elif operation is RestrictedInferenceOperation.RERANK:
        if not isinstance(payload.get("query"), str):
            raise RestrictedInferenceContractError("invalid_payload", "query must be a string")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
            raise RestrictedInferenceContractError("invalid_payload", "candidates must be a list of objects")
    elif operation is RestrictedInferenceOperation.SCORE_CHOICES:
        if not isinstance(payload.get("prompt"), str):
            raise RestrictedInferenceContractError("invalid_payload", "prompt must be a string")
        choices = _string_list(payload, "choices")
        if len(set(choices)) != len(choices):
            raise RestrictedInferenceContractError("invalid_payload", "choices must be unique")
    elif operation is RestrictedInferenceOperation.EXTRACT_FEATURES:
        if not isinstance(payload.get("text"), str):
            raise RestrictedInferenceContractError("invalid_payload", "text must be a string")
    elif operation is RestrictedInferenceOperation.RISK_SCORE and not isinstance(payload.get("input"), dict):
        raise RestrictedInferenceContractError("invalid_payload", "input must be an object")


def _execution_policy(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    policy = dict(_DEFAULT_EXECUTION_POLICY)
    if raw is not None and not isinstance(raw, Mapping):
        raise RestrictedInferenceContractError("invalid_execution_policy", "execution_policy must be an object")
    provided = dict(raw or {})
    unknown = sorted(set(provided) - _EXECUTION_POLICY_FIELDS)
    if unknown:
        raise RestrictedInferenceContractError("invalid_execution_policy", f"unknown execution fields: {unknown}")
    policy.update(provided)
    for name in ("allow_attention", "allow_cpu_fallback", "allow_hidden_states"):
        if not isinstance(policy[name], bool):
            raise RestrictedInferenceContractError("invalid_execution_policy", f"{name} must be boolean")
    device = str(policy["device"] or "").strip().lower()
    if device and device != "cpu" and device != "mps" and not re.fullmatch(r"cuda(?::[0-9]{1,3})?", device):
        raise RestrictedInferenceContractError("invalid_execution_policy", "device is invalid")
    policy["device"] = device
    for name, maximum in (
        ("max_batch_size", 1024),
        ("max_candidates", 10_000),
        ("max_input_chars", 16_000_000),
        ("max_output_dimensions", 1_000_000),
    ):
        value = policy[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise RestrictedInferenceContractError(
                "invalid_execution_policy",
                f"{name} must be between 1 and {maximum}",
            )
    return policy


def _enforce_request_limits(
    operation: RestrictedInferenceOperation,
    payload: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    if operation is RestrictedInferenceOperation.EMBED:
        batch = len(payload["texts"])
        texts = payload["texts"]
    elif operation is RestrictedInferenceOperation.RERANK:
        batch = len(payload["candidates"])
        if batch > policy["max_candidates"]:
            raise RestrictedInferenceContractError("candidate_limit_exceeded", "candidate limit exceeded")
        texts = [
            payload["query"],
            *(str(item.get("excerpt") or item.get("path") or "") for item in payload["candidates"]),
        ]
    elif operation is RestrictedInferenceOperation.SCORE_CHOICES:
        batch = len(payload["choices"])
        texts = [payload["prompt"], *payload["choices"]]
    elif operation is RestrictedInferenceOperation.RISK_SCORE:
        batch = 1
        texts = [json.dumps(payload["input"], sort_keys=True, separators=(",", ":"))]
    else:
        batch = 1
        texts = [payload["text"]]
    if batch > policy["max_batch_size"]:
        raise RestrictedInferenceContractError("batch_limit_exceeded", "batch limit exceeded")
    if sum(len(str(text)) for text in texts) > policy["max_input_chars"]:
        raise RestrictedInferenceContractError("input_limit_exceeded", "input character limit exceeded")
    serialized_size = len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if serialized_size > policy["max_input_chars"]:
        raise RestrictedInferenceContractError("input_limit_exceeded", "serialized input limit exceeded")


def _reject_generation_fields(value: Any, *, path: str = "result") -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_RESULT_KEYS:
                raise RestrictedInferenceContractError(
                    "generation_field_forbidden",
                    f"{path}.{raw_key} is forbidden in restricted inference",
                )
            _reject_generation_fields(nested, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_generation_fields(nested, path=f"{path}[{index}]")


def _wire_number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RestrictedInferenceContractError("invalid_result_value", f"{name} must be numeric")
    number = float(value)
    if minimum is not None and number < minimum:
        raise RestrictedInferenceContractError("invalid_result_value", f"{name} is below {minimum}")
    if maximum is not None and number > maximum:
        raise RestrictedInferenceContractError("invalid_result_value", f"{name} is above {maximum}")
    return number


def _exact_wire_fields(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    keys = frozenset(value)
    if keys != expected:
        raise RestrictedInferenceContractError(
            "invalid_result_shape",
            f"{name} fields mismatch: missing={sorted(expected - keys)}, unknown={sorted(keys - expected)}",
        )


def _validate_result_values(operation: RestrictedInferenceOperation, result: Mapping[str, Any]) -> None:
    if not isinstance(result["engine"], str) or not result["engine"]:
        raise RestrictedInferenceContractError("invalid_result_metadata", "engine must be a non-empty string")
    if not isinstance(result["model_id"], str) or not result["model_id"]:
        raise RestrictedInferenceContractError("invalid_result_metadata", "model_id must be a non-empty string")
    if not isinstance(result["manifest_digest"], str) or not _SHA256_RE.fullmatch(result["manifest_digest"]):
        raise RestrictedInferenceContractError("invalid_result_metadata", "manifest_digest must be a SHA-256")
    _wire_number(result["latency_ms"], "latency_ms", minimum=0.0)

    if operation is RestrictedInferenceOperation.EMBED:
        vectors = result["vectors"]
        if not isinstance(vectors, list) or not all(isinstance(vector, list) for vector in vectors):
            raise RestrictedInferenceContractError("invalid_result_value", "vectors must be a list of lists")
        for row, vector in enumerate(vectors):
            for column, number in enumerate(vector):
                _wire_number(number, f"vectors[{row}][{column}]")
    elif operation is RestrictedInferenceOperation.CLASSIFY:
        if not isinstance(result["label"], str) or not result["label"]:
            raise RestrictedInferenceContractError("invalid_result_value", "label must be a non-empty string")
        _wire_number(result["confidence"], "confidence", minimum=0.0, maximum=1.0)
        scores = result["all_scores"]
        if not isinstance(scores, dict):
            raise RestrictedInferenceContractError("invalid_result_value", "all_scores must be an object")
        for label, score in scores.items():
            if not isinstance(label, str) or not label:
                raise RestrictedInferenceContractError("invalid_result_value", "score labels must be non-empty")
            _wire_number(score, f"all_scores[{label}]", minimum=0.0, maximum=1.0)
    elif operation is RestrictedInferenceOperation.RERANK:
        items = result["items"]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RestrictedInferenceContractError("invalid_result_value", "rerank items must be objects")
        expected = frozenset({"confidence", "path", "reason_code", "record_id", "score"})
        for item in items:
            _exact_wire_fields(item, expected, "rerank item")
            if not isinstance(item["path"], str) or not isinstance(item["record_id"], str):
                raise RestrictedInferenceContractError("invalid_result_value", "rerank identity must be strings")
            if item["reason_code"] and (
                not isinstance(item["reason_code"], str) or not _REASON_CODE_RE.fullmatch(item["reason_code"])
            ):
                raise RestrictedInferenceContractError("invalid_result_value", "reason_code must be machine-readable")
            _wire_number(item["score"], "score", minimum=0.0, maximum=1.0)
            _wire_number(item["confidence"], "confidence", minimum=0.0, maximum=1.0)
    elif operation is RestrictedInferenceOperation.SCORE_CHOICES:
        items = result["items"]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RestrictedInferenceContractError("invalid_result_value", "choice items must be objects")
        for item in items:
            _exact_wire_fields(item, frozenset({"choice", "score"}), "choice item")
            if not isinstance(item["choice"], str) or not item["choice"]:
                raise RestrictedInferenceContractError("invalid_result_value", "choice must be a non-empty string")
            _wire_number(item["score"], "score")
    elif operation is RestrictedInferenceOperation.EXTRACT_FEATURES:
        vector = result["vector"]
        if not isinstance(vector, list):
            raise RestrictedInferenceContractError("invalid_result_value", "vector must be a list")
        for index, number in enumerate(vector):
            _wire_number(number, f"vector[{index}]")
        if result["dimensions"] != len(vector):
            raise RestrictedInferenceContractError("invalid_result_value", "dimensions do not match vector length")
    elif operation is RestrictedInferenceOperation.RISK_SCORE:
        _wire_number(result["risk_score"], "risk_score", minimum=0.0, maximum=1.0)
        _wire_number(result["confidence"], "confidence", minimum=0.0, maximum=1.0)
        if result["risk_category"] not in {"low", "medium", "high", "critical"}:
            raise RestrictedInferenceContractError("invalid_result_value", "risk_category is outside the fixed enum")


def _validate_result_shape(operation: RestrictedInferenceOperation, result: Mapping[str, Any]) -> None:
    keys = frozenset(result)
    allowed = _RESULT_ALLOWED_KEYS[operation] | _RESULT_METADATA_KEYS
    unknown = sorted(keys - allowed)
    missing = sorted((_RESULT_ALLOWED_KEYS[operation] | _RESULT_METADATA_KEYS) - keys)
    if unknown or missing:
        raise RestrictedInferenceContractError(
            "invalid_result_shape",
            f"result fields do not match {operation.value}: missing={missing}, unknown={unknown}",
        )
    _reject_generation_fields(result)
    _validate_result_values(operation, result)


@dataclass(frozen=True)
class RestrictedInferenceRequest:
    """One immutable unit of work created and owned by the hub."""

    request_id: str
    task_id: str
    tenant_id: str
    operation: RestrictedInferenceOperation
    payload: Mapping[str, Any]
    model_manifest_id: str
    policy_hash: str
    deadline_epoch_ms: int
    paths: tuple[str, ...] = ()
    idempotency_key: str = ""
    run_id: str = ""
    execution_policy: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise RestrictedInferenceContractError("unsupported_contract_version", self.contract_version)
        object.__setattr__(self, "request_id", _require_identifier("request_id", self.request_id))
        object.__setattr__(self, "task_id", _require_identifier("task_id", self.task_id))
        object.__setattr__(self, "tenant_id", _require_identifier("tenant_id", self.tenant_id))
        object.__setattr__(
            self,
            "model_manifest_id",
            _require_identifier("model_manifest_id", self.model_manifest_id),
        )
        object.__setattr__(self, "policy_hash", _require_identifier("policy_hash", self.policy_hash))
        if not isinstance(self.operation, RestrictedInferenceOperation):
            try:
                object.__setattr__(self, "operation", RestrictedInferenceOperation(str(self.operation)))
            except ValueError as exc:
                raise RestrictedInferenceContractError("unknown_operation", str(self.operation)) from exc
        if not isinstance(self.deadline_epoch_ms, int) or isinstance(self.deadline_epoch_ms, bool):
            raise RestrictedInferenceContractError("invalid_deadline", "deadline_epoch_ms must be an integer")
        if self.deadline_epoch_ms <= 0:
            raise RestrictedInferenceContractError("invalid_deadline", "deadline_epoch_ms must be positive")
        if isinstance(self.paths, str) or not isinstance(self.paths, (tuple, list)):
            raise RestrictedInferenceContractError("invalid_path", "paths must be a list or tuple")
        normalized_paths = tuple(str(item).strip() for item in self.paths)
        if len(normalized_paths) > 256 or any(not item or len(item) > 4096 for item in normalized_paths):
            raise RestrictedInferenceContractError("invalid_path", "paths must not contain empty entries")
        object.__setattr__(self, "paths", normalized_paths)
        if self.idempotency_key:
            object.__setattr__(
                self,
                "idempotency_key",
                _require_identifier("idempotency_key", self.idempotency_key),
            )
        if self.run_id:
            object.__setattr__(self, "run_id", _require_identifier("run_id", self.run_id))
        if not isinstance(self.payload, Mapping):
            raise RestrictedInferenceContractError("invalid_payload", "payload must be an object")
        copied_payload = _json_copy(dict(self.payload), reason_code="invalid_payload")
        _validate_request_payload(self.operation, copied_payload)
        normalized_policy = _execution_policy(self.execution_policy)
        _enforce_request_limits(self.operation, copied_payload, normalized_policy)
        object.__setattr__(self, "payload", _deep_freeze(copied_payload))
        object.__setattr__(self, "execution_policy", _deep_freeze(normalized_policy))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "operation": self.operation.value,
            "payload": _json_copy(_deep_thaw(self.payload), reason_code="invalid_payload"),
            "model_manifest_id": self.model_manifest_id,
            "policy_hash": self.policy_hash,
            "deadline_epoch_ms": self.deadline_epoch_ms,
            "paths": list(self.paths),
            "idempotency_key": self.idempotency_key,
            "run_id": self.run_id,
            "execution_policy": _deep_thaw(self.execution_policy),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RestrictedInferenceRequest":
        unknown = sorted(set(raw) - _REQUEST_WIRE_FIELDS)
        missing = sorted(_REQUEST_REQUIRED_WIRE_FIELDS - set(raw))
        if unknown or missing:
            raise RestrictedInferenceContractError(
                "invalid_request_envelope",
                f"request fields mismatch: missing={missing}, unknown={unknown}",
            )
        try:
            operation = RestrictedInferenceOperation(str(raw.get("operation") or ""))
        except ValueError as exc:
            raise RestrictedInferenceContractError("unknown_operation", str(raw.get("operation") or "")) from exc
        payload_raw = raw.get("payload")
        paths_raw = raw.get("paths")
        execution_policy_raw = raw.get("execution_policy") or {}
        if not isinstance(payload_raw, Mapping):
            raise RestrictedInferenceContractError("invalid_payload", "payload must be an object")
        if not isinstance(paths_raw, (list, tuple)):
            raise RestrictedInferenceContractError("invalid_path", "paths must be a list")
        if not isinstance(execution_policy_raw, Mapping):
            raise RestrictedInferenceContractError("invalid_execution_policy", "execution_policy must be an object")
        return cls(
            contract_version=str(raw.get("contract_version") or ""),
            request_id=str(raw.get("request_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            operation=operation,
            payload=dict(payload_raw),
            model_manifest_id=str(raw.get("model_manifest_id") or ""),
            policy_hash=str(raw.get("policy_hash") or ""),
            deadline_epoch_ms=cast(int, raw.get("deadline_epoch_ms")),
            paths=tuple(paths_raw),
            idempotency_key=str(raw.get("idempotency_key") or ""),
            run_id=str(raw.get("run_id") or ""),
            execution_policy=dict(execution_policy_raw),
        )


@dataclass(frozen=True)
class RestrictedInferenceError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_identifier("error.code", self.code))
        message = str(self.message or "").strip()
        if not message or len(message) > 1000:
            raise RestrictedInferenceContractError("invalid_error", "error.message must contain 1..1000 characters")
        object.__setattr__(self, "message", message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class RestrictedInferenceResponse:
    """Worker response constrained to non-generative result shapes."""

    request_id: str
    task_id: str
    operation: RestrictedInferenceOperation
    status: RestrictedInferenceStatus
    result: Mapping[str, Any] | None = None
    error: RestrictedInferenceError | None = None
    no_generation: bool = True
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise RestrictedInferenceContractError("unsupported_contract_version", self.contract_version)
        object.__setattr__(self, "request_id", _require_identifier("request_id", self.request_id))
        object.__setattr__(self, "task_id", _require_identifier("task_id", self.task_id))
        if not isinstance(self.operation, RestrictedInferenceOperation):
            object.__setattr__(self, "operation", RestrictedInferenceOperation(str(self.operation)))
        if not isinstance(self.status, RestrictedInferenceStatus):
            object.__setattr__(self, "status", RestrictedInferenceStatus(str(self.status)))
        if self.no_generation is not True:
            raise RestrictedInferenceContractError(
                "generation_boundary_violation",
                "restricted inference responses must carry no_generation=true",
            )
        if self.status is RestrictedInferenceStatus.SUCCEEDED:
            if self.result is None or self.error is not None:
                raise RestrictedInferenceContractError(
                    "invalid_response_state",
                    "successful responses require result and forbid error",
                )
            copied_result = _json_copy(dict(self.result), reason_code="invalid_result")
            _validate_result_shape(self.operation, copied_result)
            object.__setattr__(self, "result", _deep_freeze(copied_result))
        elif self.result is not None or self.error is None:
            raise RestrictedInferenceContractError(
                "invalid_response_state",
                "failed responses require error and forbid result",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "operation": self.operation.value,
            "status": self.status.value,
            "result": _json_copy(_deep_thaw(self.result), reason_code="invalid_result")
            if self.result is not None
            else None,
            "error": self.error.to_dict() if self.error else None,
            "no_generation": self.no_generation,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RestrictedInferenceResponse":
        unknown = sorted(set(raw) - _RESPONSE_WIRE_FIELDS)
        missing = sorted(_RESPONSE_WIRE_FIELDS - set(raw))
        if unknown or missing:
            raise RestrictedInferenceContractError(
                "invalid_response_envelope",
                f"response fields mismatch: missing={missing}, unknown={unknown}",
            )
        error_raw = raw.get("error")
        error = None
        if isinstance(error_raw, Mapping):
            if set(error_raw) != _ERROR_WIRE_FIELDS:
                raise RestrictedInferenceContractError("invalid_error", "error fields do not match the contract")
            error = RestrictedInferenceError(
                code=str(error_raw.get("code") or ""),
                message=str(error_raw.get("message") or ""),
                retryable=bool(error_raw.get("retryable", False)),
            )
        try:
            operation = RestrictedInferenceOperation(str(raw.get("operation") or ""))
            status = RestrictedInferenceStatus(str(raw.get("status") or ""))
        except ValueError as exc:
            raise RestrictedInferenceContractError("invalid_response_enum", str(exc)) from exc
        result_raw = raw.get("result")
        return cls(
            contract_version=str(raw.get("contract_version") or ""),
            request_id=str(raw.get("request_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            operation=operation,
            status=status,
            result=dict(result_raw) if isinstance(result_raw, Mapping) else None,
            error=error,
            no_generation=cast(bool, raw.get("no_generation")),
        )


def validate_response_for_request(
    request: RestrictedInferenceRequest,
    response: RestrictedInferenceResponse,
) -> None:
    """Bind a successful worker result to the exact caller-provided inputs."""

    if response.status is RestrictedInferenceStatus.FAILED:
        return
    assert response.result is not None
    result = response.result
    payload = request.payload
    if request.operation is RestrictedInferenceOperation.EMBED:
        if len(result["vectors"]) != len(payload["texts"]):
            raise RestrictedInferenceContractError(
                "embedding_count_mismatch",
                "worker must return exactly one vector per input text",
            )
        if any(len(vector) > request.execution_policy["max_output_dimensions"] for vector in result["vectors"]):
            raise RestrictedInferenceContractError(
                "output_limit_exceeded",
                "worker embedding exceeds the hub output-dimension limit",
            )
    elif request.operation is RestrictedInferenceOperation.CLASSIFY:
        allowed = set(payload["labels"])
        if result["label"] not in allowed or not set(result["all_scores"]).issubset(allowed):
            raise RestrictedInferenceContractError(
                "classification_label_outside_allowlist",
                "worker returned a label outside the caller-provided set",
            )
    elif request.operation is RestrictedInferenceOperation.SCORE_CHOICES:
        requested = list(payload["choices"])
        returned = [item["choice"] for item in result["items"]]
        if len(returned) != len(requested) or len(set(returned)) != len(returned) or set(returned) != set(requested):
            raise RestrictedInferenceContractError(
                "choice_set_mismatch",
                "worker must return every caller-provided choice exactly once",
            )
    elif request.operation is RestrictedInferenceOperation.RERANK:
        candidates = list(payload["candidates"])
        if len(result["items"]) > len(candidates):
            raise RestrictedInferenceContractError(
                "invented_rerank_candidate",
                "worker returned more rerank items than candidates",
            )
        allowed_ids = {str(item.get("record_id") or "") for item in candidates if item.get("record_id")}
        allowed_paths = {str(item.get("path") or "") for item in candidates if item.get("path")}
        for item in result["items"]:
            if item["record_id"] and allowed_ids and item["record_id"] not in allowed_ids:
                raise RestrictedInferenceContractError("invented_rerank_candidate", item["record_id"])
            if item["path"] and allowed_paths and item["path"] not in allowed_paths:
                raise RestrictedInferenceContractError("invented_rerank_candidate", item["path"])
    elif request.operation is RestrictedInferenceOperation.EXTRACT_FEATURES:
        if result["dimensions"] > request.execution_policy["max_output_dimensions"]:
            raise RestrictedInferenceContractError(
                "output_limit_exceeded",
                "worker feature vector exceeds the hub output-dimension limit",
            )
