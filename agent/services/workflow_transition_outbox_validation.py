"""Fail-closed JSON and identity primitives for workflow transition contracts.

The helpers freeze, thaw, bound and digest JSON payloads and validate the
scalar identity fields shared by transition, effect and digest builders.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,159}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_EFFECTS = 64
_MAX_EFFECT_PAYLOAD_BYTES = 524_288
_MAX_RESULT_PAYLOAD_BYTES = 524_288
_MAX_FINALIZATION_RESULT_PAYLOAD_BYTES = 1_100_000
_MAX_STATUS_BYTES = 524_288
_MAX_IDEMPOTENCY_KEY_CHARS = 512
_MAX_STAGE_ATTEMPTS = 1_000
_EFFECT_RESULT_MODES = frozenset({"adopt", "execute"})

FrozenJsonMapping = Mapping[str, Any]


class WorkflowTransitionError(RuntimeError):
    """Stable fail-closed transition validation or persistence failure."""


def thaw_json(value: Any) -> Any:
    """Return a detached JSON-compatible copy of a frozen contract value."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def _validated_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
    empty: bool = True,
) -> dict[str, Any]:
    try:
        thawed = thaw_json(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid") from exc
    if not isinstance(thawed, dict) or (not empty and not thawed):
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
    _validate_json_value(thawed, reason=reason)
    try:
        size = len(canonical_json(thawed).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid") from exc
    if size > maximum:
        raise WorkflowTransitionError(f"workflow_transition_{reason}_too_large")
    return thawed


def _validate_json_value(value: Any, *, reason: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, reason=reason)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")
            _validate_json_value(item, reason=reason)
        return
    raise WorkflowTransitionError(f"workflow_transition_{reason}_invalid")


def _freeze_json_mapping(value: Mapping[str, Any]) -> FrozenJsonMapping:
    frozen = _freeze_json(dict(value))
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by caller
        raise WorkflowTransitionError("workflow_transition_mapping_invalid")
    return frozen


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _digest(value: Any, *, namespace: str) -> str:
    framed = {
        "namespace": namespace,
        "value": thaw_json(value),
    }
    return hashlib.sha256(canonical_json(framed).encode("utf-8")).hexdigest()


def _opaque_id(namespace: str, *parts: object) -> str:
    framed = "\x1f".join([namespace, *(str(part) for part in parts)])
    return f"{namespace}-{hashlib.sha256(framed.encode('utf-8')).hexdigest()}"


def _identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _bounded_text(value: Any, maximum: int, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value)
    ):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return value


def _timestamp(value: Any, field_name: str, *, positive: bool) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < (1e-12 if positive else 0.0)
    ):
        raise WorkflowTransitionError(f"workflow_transition_{field_name}_invalid")
    return float(value)


def _reason_code(value: Any) -> str:
    if not isinstance(value, str) or _REASON_RE.fullmatch(value) is None:
        raise WorkflowTransitionError("workflow_transition_reason_code_invalid")
    return value
