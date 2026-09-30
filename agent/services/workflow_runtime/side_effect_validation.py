"""Primitive validators and the namespaced digest for the side-effect ledger.

Pure functions only; shared by the ledger record model and the transition
authorization contract so neither owns the other's validation rules.
"""

from __future__ import annotations

import hashlib
import math
import re

from agent.services.workflow_runtime._serialization import canonical_json

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MAX_COUNTER = 2**63 - 1


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _bounded_text(value: object, maximum: int, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid") from exc
    return value


def _sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _runtime_identity(value: object) -> str:
    runtime_id = _identity(value, "runtime_id")
    if len(runtime_id) > 64:
        raise ValueError("workflow_transition_side_effect_authorization_runtime_id_invalid")
    return runtime_id


def _positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _MAX_COUNTER:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _nonnegative_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_COUNTER:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _positive_timestamp(value: object, reason: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return float(value)


def _namespaced_digest(namespace: str, payload: object) -> str:
    framed = f"{namespace}\x00{canonical_json(payload)}".encode("utf-8")
    return hashlib.sha256(framed).hexdigest()
