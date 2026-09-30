"""Bounded JSON copying and scalar validation for staged authorization grants.

Package-internal helpers of ``workflow_transition_authorization_grant``.  Every
failure raises :class:`WorkflowTransitionAuthorizationGrantError` with a stable
``workflow_transition_authorization_grant_*`` reason code.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_transition_effect_proofs import WorkflowTransitionEffectScalars
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderProfileAttemptPlanEntry,
)

_MAX_ENVELOPE_BYTES = 240_000
_MAX_EFFECT_BYTES = 260_000
_MAX_JSON_DEPTH = 32
_MAX_JSON_ITEMS = 10_000
_MAX_TEXT_BYTES = 240_000
_MAX_TTL_SECONDS = 31_536_000.0
_MAX_COUNTER = 2**63 - 1


class WorkflowTransitionAuthorizationGrantError(ValueError):
    """Stable fail-closed staged-grant or semantic-proof error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionAuthorizationGrantError,
    prefix="workflow_transition_authorization_grant",
)


@dataclass(slots=True)
class _JsonBudget:
    remaining_items: int = _MAX_JSON_ITEMS
    remaining_text_bytes: int = _MAX_TEXT_BYTES

    def item(self) -> None:
        self.remaining_items -= 1
        if self.remaining_items < 0:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")

    def text(self, value: str) -> str:
        self.item()
        try:
            self.remaining_text_bytes -= len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_json_invalid"
            ) from exc
        if self.remaining_text_bytes < 0:
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_too_large")
        return value


def _bounded_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
) -> dict[str, Any]:
    copied = _copy_json(value, depth=0, budget=_JsonBudget())
    if not isinstance(copied, dict):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    try:
        encoded = canonical_json(copied).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            f"workflow_transition_authorization_grant_{reason}_invalid"
        ) from exc
    if len(encoded) > maximum:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_too_large")
    return copied


def _input_mapping(value: Any, *, reason: str) -> dict[str, Any]:
    copied = _copy_json(value, depth=0, budget=_JsonBudget())
    if not isinstance(copied, dict):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return copied


def _text_sequence(value: Any, *, reason: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    if len(value) > 1_000:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return [_text(item, 512, reason) for item in value]


def _mapping_sequence(value: Any, *, reason: str) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    if len(value) > 8:
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return [_input_mapping(item, reason=reason) for item in value]


def _provider_binding_sequence(value: Any) -> list[dict[str, Any]]:
    raw_values = _mapping_sequence(
        value,
        reason="allowed_provider_bindings",
    )
    canonical: list[dict[str, Any]] = []
    for raw in raw_values:
        try:
            normalized = ProviderBindingAuthorization.from_mapping(raw).to_dict()
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_allowed_provider_bindings_invalid"
            ) from exc
        if canonical_json(raw) != canonical_json(normalized):
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_allowed_provider_bindings_invalid"
            )
        canonical.append(normalized)
    return canonical


def _provider_attempt_sequence(value: Any) -> list[dict[str, Any]]:
    raw_values = _mapping_sequence(
        value,
        reason="provider_attempt_plan",
    )
    canonical: list[dict[str, Any]] = []
    for raw in raw_values:
        try:
            normalized = ProviderProfileAttemptPlanEntry.from_mapping(raw).to_dict()
        except Exception as exc:
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_provider_attempt_plan_invalid"
            ) from exc
        if canonical_json(raw) != canonical_json(normalized):
            raise WorkflowTransitionAuthorizationGrantError(
                "workflow_transition_authorization_grant_provider_attempt_plan_invalid"
            )
        canonical.append(normalized)
    return canonical


def _budget_mapping(value: Any) -> dict[str, int | float]:
    copied = _input_mapping(value, reason="budgets")
    if len(copied) > 256:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_budgets_invalid")
    budgets: dict[str, int | float] = {}
    for name, amount in copied.items():
        _text(name, 256, "budget_name")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or amount < 0
            or amount > _MAX_COUNTER
            or (isinstance(amount, float) and not math.isfinite(amount))
            or (name == "provider_attempts" and type(amount) is not int)
        ):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_budgets_invalid")
        budgets[name] = amount
    return budgets


def _assert_provider_attempt_budget(
    *,
    budgets: Mapping[str, int | float],
    attempt_plan: Sequence[Mapping[str, Any]],
) -> None:
    if not attempt_plan:
        return
    maximum = budgets.get("provider_attempts")
    planned = sum(item["maximum_attempts"] for item in attempt_plan)
    if type(maximum) is not int or maximum != planned:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_provider_attempt_plan_budget_invalid"
        )


def _copy_json(value: Any, *, depth: int, budget: _JsonBudget) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
    if isinstance(value, str):
        return budget.text(value)
    budget.item()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")
            budget.text(key)
            copied[key] = _copy_json(item, depth=depth + 1, budget=budget)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, depth=depth + 1, budget=budget) for item in value]
    raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_json_invalid")


def _identity(value: Any, reason: str) -> str:
    return _SCALARS.identity(value, reason)


def _scope_identity(value: Any, reason: str) -> str:
    try:
        normalized = require_canonical_identity(value, field_name=reason)
        return _identity(normalized, reason)
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            f"workflow_transition_authorization_grant_{reason}_invalid"
        ) from exc


def _text(value: Any, maximum: int, reason: str) -> str:
    return _SCALARS.text(value, reason, maximum=maximum)


def _sha256(value: Any, reason: str) -> str:
    return _SCALARS.sha256(value, reason)


def _positive_integer(value: Any, reason: str) -> int:
    return _SCALARS.positive_integer(value, reason, maximum=_MAX_COUNTER)


def _positive_timestamp(value: Any, reason: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > _MAX_COUNTER
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise WorkflowTransitionAuthorizationGrantError(f"workflow_transition_authorization_grant_{reason}_invalid")
    return float(value)


def _ttl(value: Any) -> float:
    ttl = _positive_timestamp(value, "ttl_seconds")
    if ttl > _MAX_TTL_SECONDS:
        raise WorkflowTransitionAuthorizationGrantError("workflow_transition_authorization_grant_ttl_seconds_invalid")
    return ttl


def _clock_value(clock: Callable[[], float]) -> float:
    try:
        return _positive_timestamp(clock(), "clock")
    except Exception as exc:
        raise WorkflowTransitionAuthorizationGrantError(
            "workflow_transition_authorization_grant_clock_invalid"
        ) from exc


def _opaque_id(prefix: str, namespace: str, *parts: str) -> str:
    payload = "\x00".join((namespace, *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "WorkflowTransitionAuthorizationGrantError",
]
