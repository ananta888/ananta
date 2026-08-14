"""Runtime-neutral effect execution and adoption contracts for Hub transitions.

This module defines only closed contracts.  It deliberately contains no
runtime adapters, service-locator defaults, background runner, or composition.
The Hub runner remains responsible for durable effect/result digests and the
terminal transition outcome fingerprint.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, final, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_EFFECT_KINDS,
    TRANSITION_KINDS,
    TRANSITION_RUNTIMES,
    TRANSITION_STATE_APPLYING,
    WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionError,
    WorkflowTransitionSnapshot,
)
from agent.services.workflow_transition_outbox import (
    workflow_transition_effect_result_envelope as _domain_effect_result_envelope,
)
from agent.services.workflow_transition_outbox import (
    workflow_transition_effect_stage_attempt_count as _domain_stage_attempt_count,
)

_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,159}$")
_MAX_RESULT_BYTES = 524_288
_MAX_PROOF_BYTES = 524_288
_MAX_STATUS_BYTES = 524_288
_MAX_CHECKPOINT_CHARS = 512
_MAX_JSON_DEPTH = 32
_MAX_ATTEMPTS = 1_000
_MAX_RETRY_SECONDS = 31_536_000.0

FrozenJsonMapping: TypeAlias = Mapping[str, Any]


class WorkflowTransitionEffectExecutionError(ValueError):
    """Stable fail-closed contract or registry error."""


@final
@dataclass(frozen=True, slots=True)
class EffectAlreadyApplied:
    """Authoritative observation of an externally completed effect."""

    result_payload: FrozenJsonMapping
    proof_payload: FrozenJsonMapping

    def __post_init__(self) -> None:
        result = _validated_mapping(
            self.result_payload,
            maximum=_MAX_RESULT_BYTES,
            reason="already_applied_result",
        )
        proof = _validated_mapping(
            self.proof_payload,
            maximum=_MAX_PROOF_BYTES,
            reason="already_applied_proof",
        )
        workflow_transition_effect_result_envelope(
            mode="adopt",
            result_payload=result,
            proof_payload=proof,
            stage_attempt_count=1,
        )
        object.__setattr__(self, "result_payload", result)
        object.__setattr__(self, "proof_payload", proof)


@final
@dataclass(frozen=True, slots=True)
class EffectExecutable:
    """Authoritative nonempty evidence that execution may proceed."""

    proof_payload: FrozenJsonMapping

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proof_payload",
            _validated_mapping(
                self.proof_payload,
                maximum=_MAX_PROOF_BYTES,
                reason="executable_proof",
            ),
        )


@final
@dataclass(frozen=True, slots=True)
class EffectRetry:
    """Explicit no-mutation observation or execution result."""

    reason_code: str

    def __post_init__(self) -> None:
        _reason_code(self.reason_code)


@final
@dataclass(frozen=True, slots=True)
class EffectQuarantine:
    """Explicit terminal fail-closed result requiring durable quarantine."""

    reason_code: str

    def __post_init__(self) -> None:
        _reason_code(self.reason_code)


@final
@dataclass(frozen=True, slots=True)
class EffectApplied:
    """Successful execution payload; the Hub runner derives its digest."""

    result_payload: FrozenJsonMapping
    proof_payload: FrozenJsonMapping

    def __post_init__(self) -> None:
        result = _validated_mapping(
            self.result_payload,
            maximum=_MAX_RESULT_BYTES,
            reason="applied_result",
        )
        proof = _validated_mapping(
            self.proof_payload,
            maximum=_MAX_PROOF_BYTES,
            reason="applied_proof",
        )
        workflow_transition_effect_result_envelope(
            mode="execute",
            result_payload=result,
            proof_payload=proof,
            stage_attempt_count=1,
        )
        object.__setattr__(self, "result_payload", result)
        object.__setattr__(self, "proof_payload", proof)


EffectObservationResult: TypeAlias = EffectAlreadyApplied | EffectExecutable | EffectRetry | EffectQuarantine
EffectExecutionResult: TypeAlias = EffectApplied | EffectRetry | EffectQuarantine


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectObservation:
    """Pre-begin observation of a planned effect or an older generation."""

    transition: WorkflowTransition
    effect: WorkflowTransitionEffect
    claim_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.transition, WorkflowTransition) or not isinstance(self.effect, WorkflowTransitionEffect):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_observation_invalid")
        observable_effect = (self.effect.state == EFFECT_STATE_PLANNED and self.effect.applied_generation == 0) or (
            self.effect.state == EFFECT_STATE_APPLYING and self.effect.applied_generation < self.claim_generation
        )
        if (
            self.transition.state != TRANSITION_STATE_APPLYING
            or self.effect.transition_id != self.transition.transition_id
            or self.effect.kind == EFFECT_BINDING_FINALIZE
            or not _positive_integer(self.claim_generation)
            or self.transition.claim_generation != self.claim_generation
            or not observable_effect
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_observation_invalid")


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectAttempt:
    """One generation-fenced effect attempt after durable ``begin_effect``."""

    transition: WorkflowTransition
    effect: WorkflowTransitionEffect
    claim_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.transition, WorkflowTransition) or not isinstance(self.effect, WorkflowTransitionEffect):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_attempt_invalid")
        if (
            self.transition.state != TRANSITION_STATE_APPLYING
            or self.effect.state != EFFECT_STATE_APPLYING
            or self.effect.transition_id != self.transition.transition_id
            or self.effect.kind == EFFECT_BINDING_FINALIZE
            or not _positive_integer(self.claim_generation)
            or self.transition.claim_generation != self.claim_generation
            or self.effect.applied_generation != self.claim_generation
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_attempt_invalid")


@runtime_checkable
class WorkflowTransitionHeartbeatContext(Protocol):
    """Cooperative lease renewal capability supplied by the Hub runner."""

    def heartbeat(self) -> None: ...


@runtime_checkable
class WorkflowTransitionEffectObservationPort(Protocol):
    """Read or adopt external proof before any new effect execution."""

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectObservationResult: ...


@runtime_checkable
class WorkflowTransitionEffectExecutionPort(Protocol):
    """Execute only after an observer supplied authoritative executable proof."""

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectExecutionResult: ...


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectHandler:
    observation: WorkflowTransitionEffectObservationPort
    execution: WorkflowTransitionEffectExecutionPort

    def __post_init__(self) -> None:
        if not isinstance(self.observation, WorkflowTransitionEffectObservationPort) or not isinstance(
            self.execution, WorkflowTransitionEffectExecutionPort
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_handler_invalid")


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionEffectRegistration:
    runtime_id: str
    effect_kind: str
    handler: WorkflowTransitionEffectHandler

    def __post_init__(self) -> None:
        _runtime_id(self.runtime_id)
        _effect_kind(self.effect_kind, allow_finalize=False)
        if not isinstance(self.handler, WorkflowTransitionEffectHandler):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_handler_invalid")


@final
@dataclass(frozen=True, slots=True, init=False)
class WorkflowTransitionEffectExecutorRegistry:
    """Immutable exact-pair registry; no runtime or kind fallback is allowed."""

    _handlers: Mapping[tuple[str, str], WorkflowTransitionEffectHandler]

    def __init__(self, registrations: Sequence[WorkflowTransitionEffectRegistration]) -> None:
        if (
            not isinstance(registrations, Sequence)
            or isinstance(registrations, (str, bytes))
            or len(registrations) > len(TRANSITION_RUNTIMES) * (len(TRANSITION_EFFECT_KINDS) - 1)
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registrations_invalid")
        values = tuple(registrations)
        handlers: dict[tuple[str, str], WorkflowTransitionEffectHandler] = {}
        for registration in values:
            if not isinstance(registration, WorkflowTransitionEffectRegistration):
                raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registration_invalid")
            key = (registration.runtime_id, registration.effect_kind)
            if key in handlers:
                raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registration_duplicate")
            handlers[key] = registration.handler
        object.__setattr__(self, "_handlers", MappingProxyType(handlers))

    def resolve(self, *, runtime_id: str, effect_kind: str) -> WorkflowTransitionEffectHandler:
        runtime = _runtime_id(runtime_id)
        kind = _effect_kind(effect_kind, allow_finalize=False)
        handler = self._handlers.get((runtime, kind))
        if handler is None:
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_executor_missing")
        return handler


@final
@dataclass(frozen=True, slots=True)
class FinalizationObserved:
    """Read-only authoritative raw binding evidence for store finalization."""

    binding_status: FrozenJsonMapping
    checkpoint_ref: str
    proof_payload: FrozenJsonMapping

    def __post_init__(self) -> None:
        status = _validated_mapping(
            self.binding_status,
            maximum=_MAX_STATUS_BYTES,
            reason="finalization_status",
        )
        checkpoint = _checkpoint_ref(self.checkpoint_ref)
        proof = _validated_mapping(
            self.proof_payload,
            maximum=_MAX_PROOF_BYTES,
            reason="finalization_proof",
        )
        revision = status.get("revision")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or status.get("checkpoint_ref") != checkpoint
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_evidence_invalid")
        object.__setattr__(self, "binding_status", status)
        object.__setattr__(self, "proof_payload", proof)


@final
@dataclass(frozen=True, slots=True)
class FinalizationRetry:
    reason_code: str

    def __post_init__(self) -> None:
        _reason_code(self.reason_code)


@final
@dataclass(frozen=True, slots=True)
class FinalizationQuarantine:
    reason_code: str

    def __post_init__(self) -> None:
        _reason_code(self.reason_code)


FinalizationObservationResult: TypeAlias = FinalizationObserved | FinalizationRetry | FinalizationQuarantine


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionFinalizationAttempt:
    """Generation-fenced read-only observation after all non-final effects."""

    snapshot: WorkflowTransitionSnapshot
    claim_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, WorkflowTransitionSnapshot) or not _positive_integer(self.claim_generation):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_attempt_invalid")
        transition = self.snapshot.transition
        effects = self.snapshot.effects
        final_effect = effects[-1]
        if (
            transition.state != TRANSITION_STATE_APPLYING
            or transition.claim_generation != self.claim_generation
            or final_effect.kind != EFFECT_BINDING_FINALIZE
            or any(effect.state != EFFECT_STATE_APPLIED for effect in effects[:-1])
            or final_effect.state != EFFECT_STATE_PLANNED
            or final_effect.applied_generation != 0
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_attempt_invalid")


@runtime_checkable
class WorkflowTransitionFinalizationObservationPort(Protocol):
    """Observe authoritative raw completion evidence without mutating runtime state."""

    def observe(
        self,
        attempt: WorkflowTransitionFinalizationAttempt,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> FinalizationObservationResult: ...


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionFinalizationRegistration:
    runtime_id: str
    transition_kind: str
    observation: WorkflowTransitionFinalizationObservationPort

    def __post_init__(self) -> None:
        _runtime_id(self.runtime_id)
        _transition_kind(self.transition_kind)
        if not isinstance(self.observation, WorkflowTransitionFinalizationObservationPort):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_observer_invalid")


@final
@dataclass(frozen=True, slots=True, init=False)
class WorkflowTransitionFinalizationObserverRegistry:
    """Immutable exact runtime/transition-kind finalization observer registry."""

    _observers: Mapping[tuple[str, str], WorkflowTransitionFinalizationObservationPort]

    def __init__(self, registrations: Sequence[WorkflowTransitionFinalizationRegistration]) -> None:
        if (
            not isinstance(registrations, Sequence)
            or isinstance(registrations, (str, bytes))
            or len(registrations) > len(TRANSITION_RUNTIMES) * len(TRANSITION_KINDS)
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_registrations_invalid")
        values = tuple(registrations)
        observers: dict[tuple[str, str], WorkflowTransitionFinalizationObservationPort] = {}
        for registration in values:
            if not isinstance(registration, WorkflowTransitionFinalizationRegistration):
                raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_registration_invalid")
            key = (registration.runtime_id, registration.transition_kind)
            if key in observers:
                raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_registration_duplicate")
            observers[key] = registration.observation
        object.__setattr__(self, "_observers", MappingProxyType(observers))

    def resolve(
        self,
        *,
        runtime_id: str,
        transition_kind: str,
    ) -> WorkflowTransitionFinalizationObservationPort:
        runtime = _runtime_id(runtime_id)
        kind = _transition_kind(transition_kind)
        observer = self._observers.get((runtime, kind))
        if observer is None:
            raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_observer_missing")
        return observer


@final
@dataclass(frozen=True, slots=True)
class RetryAt:
    retry_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "retry_at", _timestamp(self.retry_at, reason="retry_at"))


@final
@dataclass(frozen=True, slots=True)
class RetryExhausted:
    """Typed signal that no further automatic attempt is authorized."""


WorkflowTransitionRetryResult: TypeAlias = RetryAt | RetryExhausted


class WorkflowTransitionRetryPolicy(Protocol):
    """Pure retry scheduling policy; callers provide the decision timestamp."""

    def authorize_attempt(self, *, attempt_count: int) -> bool: ...

    def next_retry(self, *, attempt_count: int, decision_at: float) -> WorkflowTransitionRetryResult: ...


@final
@dataclass(frozen=True, slots=True)
class BoundedWorkflowTransitionRetryPolicy:
    """Deterministic bounded exponential retry policy with explicit configuration."""

    maximum_attempts: int
    initial_delay_seconds: float
    multiplier: float
    maximum_delay_seconds: float

    def __post_init__(self) -> None:
        if (
            not _positive_integer(self.maximum_attempts)
            or self.maximum_attempts > _MAX_ATTEMPTS
            or not _finite_number(self.initial_delay_seconds)
            or not _finite_number(self.multiplier)
            or not _finite_number(self.maximum_delay_seconds)
            or float(self.initial_delay_seconds) <= 0
            or float(self.multiplier) < 1
            or float(self.maximum_delay_seconds) < float(self.initial_delay_seconds)
            or float(self.maximum_delay_seconds) > _MAX_RETRY_SECONDS
        ):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_retry_policy_invalid")

    def next_retry(self, *, attempt_count: int, decision_at: float) -> WorkflowTransitionRetryResult:
        if not _positive_integer(attempt_count):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_retry_attempt_invalid")
        decision = _timestamp(decision_at, reason="retry_decision_at")
        if attempt_count >= self.maximum_attempts:
            return RetryExhausted()

        delay = float(self.initial_delay_seconds)
        maximum = float(self.maximum_delay_seconds)
        multiplier = float(self.multiplier)
        for _ in range(attempt_count - 1):
            if delay >= maximum / multiplier:
                delay = maximum
                break
            delay *= multiplier
        retry_at = decision + min(delay, maximum)
        if not math.isfinite(retry_at):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_retry_at_invalid")
        return RetryAt(retry_at)

    def authorize_attempt(self, *, attempt_count: int) -> bool:
        if not _positive_integer(attempt_count):
            raise WorkflowTransitionEffectExecutionError("workflow_transition_retry_attempt_invalid")
        return attempt_count <= self.maximum_attempts


def workflow_transition_effect_result_envelope(
    *,
    mode: str,
    result_payload: Mapping[str, Any],
    proof_payload: Mapping[str, Any],
    stage_attempt_count: int,
) -> dict[str, Any]:
    """Validate through the durable outbox-domain envelope authority."""

    try:
        return _domain_effect_result_envelope(
            mode=mode,
            result_payload=result_payload,
            proof_payload=proof_payload,
            stage_attempt_count=stage_attempt_count,
        )
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionEffectExecutionError(str(exc)) from exc


def workflow_transition_effect_stage_attempt_count(
    result_payload: Mapping[str, Any],
) -> int:
    """Validate a persisted result envelope and return its positive stage count."""

    try:
        return _domain_stage_attempt_count(result_payload)
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionEffectExecutionError(str(exc)) from exc


def _runtime_id(value: Any) -> str:
    if not isinstance(value, str) or value not in TRANSITION_RUNTIMES:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registry_runtime_invalid")
    return value


def _effect_kind(value: Any, *, allow_finalize: bool) -> str:
    if not isinstance(value, str) or value not in TRANSITION_EFFECT_KINDS:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registry_kind_invalid")
    if not allow_finalize and value == EFFECT_BINDING_FINALIZE:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_registry_finalize_forbidden")
    return value


def _transition_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in TRANSITION_KINDS:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_transition_kind_invalid")
    return value


def _reason_code(value: Any) -> str:
    if not isinstance(value, str) or _REASON_RE.fullmatch(value) is None:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_reason_code_invalid")
    return value


def _checkpoint_ref(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_CHECKPOINT_CHARS or "\x00" in value:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_finalization_checkpoint_invalid")
    return value


def _positive_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _timestamp(value: Any, *, reason: str) -> float:
    if not _finite_number(value) or float(value) < 0:
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
    return float(value)


def _validated_mapping(
    value: Any,
    *,
    maximum: int,
    reason: str,
) -> FrozenJsonMapping:
    if not isinstance(value, Mapping):
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
    copied = _copy_json(value, reason=reason, depth=0)
    if not isinstance(copied, dict) or not copied:
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
    try:
        size = len(canonical_json(copied).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid") from exc
    if size > maximum:
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_too_large")
    frozen = _freeze_json(copied)
    if not isinstance(frozen, Mapping):  # pragma: no cover - copied is a dict
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
    return frozen


def _copy_json(value: Any, *, reason: str, depth: int) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")
            copied[key] = _copy_json(item, reason=reason, depth=depth + 1)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, reason=reason, depth=depth + 1) for item in value]
    raise WorkflowTransitionEffectExecutionError(f"workflow_transition_{reason}_invalid")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


__all__ = [
    "BoundedWorkflowTransitionRetryPolicy",
    "EffectAlreadyApplied",
    "EffectApplied",
    "EffectExecutable",
    "EffectExecutionResult",
    "EffectObservationResult",
    "EffectQuarantine",
    "EffectRetry",
    "FinalizationObservationResult",
    "FinalizationObserved",
    "FinalizationQuarantine",
    "FinalizationRetry",
    "RetryAt",
    "RetryExhausted",
    "WorkflowTransitionEffectAttempt",
    "WorkflowTransitionEffectExecutionError",
    "WorkflowTransitionEffectExecutionPort",
    "WorkflowTransitionEffectExecutorRegistry",
    "WorkflowTransitionEffectHandler",
    "WorkflowTransitionEffectObservation",
    "WorkflowTransitionEffectObservationPort",
    "WorkflowTransitionEffectRegistration",
    "WorkflowTransitionFinalizationAttempt",
    "WorkflowTransitionFinalizationObservationPort",
    "WorkflowTransitionFinalizationObserverRegistry",
    "WorkflowTransitionFinalizationRegistration",
    "WorkflowTransitionHeartbeatContext",
    "WorkflowTransitionRetryPolicy",
    "WorkflowTransitionRetryResult",
    "WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA",
    "workflow_transition_effect_result_envelope",
    "workflow_transition_effect_stage_attempt_count",
]
