"""Limits, canonical JSON digests and closed-field validators of the speech-adaptation contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


CONTRACT_VERSION = "ananta.speech-adaptation.v1"
TRAIN_JOB_TYPE = "speech_adaptation_train"
RESULT_TYPE = "speech_adaptation_result"
SUPPORTED_BACKENDS = frozenset({"mock", "openvoice_v2"})
SUPPORTED_DIRECTIONS = frozenset({"sender_to_receiver", "receiver_to_sender"})
SUPPORTED_SCENARIOS = frozenset(
    {
        "success",
        "dataset_only",
        "cancel",
        "deadline",
        "lease_lost",
        "checkpoint_resume",
        "evaluation_fail",
        "publish_fail",
        "subprocess_cancel",
    }
)


MAX_STEPS = 100_000
MAX_BATCH_SIZE = 64
MAX_CHECKPOINTS = 128
MAX_WALL_SECONDS = 8 * 60 * 60
MAX_RAM_BYTES = 512 * 1024**3
MAX_VRAM_BYTES = 256 * 1024**3
MAX_DISK_BYTES = 1024**4
MAX_ARTIFACT_BYTES = 8 * 1024**3
MAX_EVENTS = 10_000
MAX_DEADLINE_AHEAD_MS = 24 * 60 * 60 * 1000


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REF_RE = re.compile(r"^artifact://[A-Za-z0-9][A-Za-z0-9_./:-]{0,500}$")


class SpeechAdaptationContractError(ValueError):
    """Stable fail-closed contract rejection safe for transport."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.code = reason_code
        self.status_code = status_code
        self.http_status = status_code
        self.retryable = retryable


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpeechAdaptationContractError(
            "speech_contract_not_canonical",
            "speech adaptation contract must be finite canonical JSON",
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be an object")
    return value


def _closed(value: Any, field: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    result = _mapping(value, field)
    if any(not isinstance(key, str) for key in result):
        raise SpeechAdaptationContractError(
            "speech_contract_unknown_field",
            f"{field} contains a non-string field name",
        )
    unknown = sorted(set(result) - allowed)
    if unknown:
        raise SpeechAdaptationContractError(
            "speech_contract_unknown_field",
            f"{field} contains unknown fields: {', '.join(unknown[:10])}",
        )
    return result


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_invalid",
            f"{field} is required and must contain at most {maximum} characters",
        )
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, maximum=192)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise SpeechAdaptationContractError("speech_contract_identifier_invalid", f"{field} is invalid")
    return result


def _digest(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not _DIGEST_RE.fullmatch(result):
        raise SpeechAdaptationContractError(
            "speech_contract_digest_invalid",
            f"{field} must be a lowercase SHA-256 digest",
        )
    return result


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_limit_exceeded",
            f"{field} must be between {minimum} and {maximum}",
        )
    return value


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_limit_exceeded",
            f"{field} must be finite and between {minimum} and {maximum}",
        )
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be a boolean")
    return value


def _artifact_ref(value: Any, field: str, *, prefix: str) -> str:
    result = _text(value, field)
    if not _ARTIFACT_REF_RE.fullmatch(result) or ".." in result.split("/") or not result.startswith(prefix):
        raise SpeechAdaptationContractError(
            "speech_contract_artifact_ref_invalid",
            f"{field} must be an immutable {prefix} reference",
        )
    forbidden = ("browser", "peer-buffer", "quarantine", "delay-buffer", "live-buffer")
    if any(part in result.casefold() for part in forbidden):
        raise SpeechAdaptationContractError(
            "speech_contract_mutable_source_forbidden",
            f"{field} references a mutable source",
        )
    return result


__all__ = [
    "CONTRACT_VERSION",
    "MAX_ARTIFACT_BYTES",
    "MAX_BATCH_SIZE",
    "MAX_CHECKPOINTS",
    "MAX_DEADLINE_AHEAD_MS",
    "MAX_DISK_BYTES",
    "MAX_EVENTS",
    "MAX_RAM_BYTES",
    "MAX_STEPS",
    "MAX_VRAM_BYTES",
    "MAX_WALL_SECONDS",
    "RESULT_TYPE",
    "SUPPORTED_BACKENDS",
    "SUPPORTED_DIRECTIONS",
    "SUPPORTED_SCENARIOS",
    "SpeechAdaptationContractError",
    "TRAIN_JOB_TYPE",
    "canonical_json",
    "canonical_sha256",
]
