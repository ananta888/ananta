"""Schema identifiers, bounds, and exact scalar validators for Hub-owned execution ownership."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.ownership_transition_errors import WorkflowTransitionOwnershipReservationConflict

EXECUTION_OWNERSHIP_SCHEMA = "ananta.execution_ownership.v1"
RETRY_BUDGET_SCHEMA = "ananta.retry_budget.v1"
OWNERSHIP_STATUSES = frozenset({"active", "completed", "failed", "orphaned", "dead_letter"})
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA = "ananta.workflow_transition_ownership_reservation_intent.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA = "ananta.workflow_transition_ownership_reservation_receipt.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA = (
    "ananta.workflow_transition_ownership_reservation_observation.v1"
)
_OWNERSHIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_OWNERSHIP_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_OWNERSHIP_MAX_COUNTER = 2**63 - 1
_OWNERSHIP_MAX_LEGACY_REVISION = 2_147_483_647
_OWNERSHIP_MAX_RETRIES = 2_147_483_647
_OWNERSHIP_RETRY_CATEGORY = "hub_task"


def _ownership_namespaced_digest(value: Mapping[str, Any], *, namespace: str) -> str:
    return hashlib.sha256(f"{namespace}\n{canonical_json(dict(value))}".encode("utf-8")).hexdigest()


def _ownership_opaque_id(prefix: str, *parts: object) -> str:
    payload = canonical_json({"namespace": prefix, "parts": list(parts)})
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _ownership_identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _OWNERSHIP_ID_RE.fullmatch(value) is None:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_legacy_text(value: object, reason: str, *, empty: bool) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid") from exc
    return value


def _ownership_status(value: object) -> str:
    if not isinstance(value, str) or value not in OWNERSHIP_STATUSES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_status_invalid")
    return value


def _ownership_exact_schema(value: object, expected: str) -> str:
    if value != expected or not isinstance(value, str):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_schema_unsupported")
    return value


def _ownership_sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _OWNERSHIP_SHA256_RE.fullmatch(value) is None:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _OWNERSHIP_MAX_COUNTER:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_positive_legacy_counter(value: object, reason: str) -> int:
    result = _ownership_positive_integer(value, reason)
    if result > _OWNERSHIP_MAX_LEGACY_REVISION:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return result


def _ownership_non_negative_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _OWNERSHIP_MAX_COUNTER:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_retry_maximum(value: object) -> int:
    maximum = _ownership_non_negative_integer(value, "maximum_retries")
    if maximum > _OWNERSHIP_MAX_RETRIES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_maximum_retries_invalid")
    return maximum


def _ownership_exact_positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value


def _ownership_exact_timestamp(value: object, reason: str) -> float:
    return _ownership_exact_positive_float(value, reason)


def _ownership_finite_timestamp(value: object, reason: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return value
