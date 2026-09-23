"""Hub-side mapping of AudioDecision outcomes onto typed hub routes.

The AudioDecision provider (``voice_runtime.backends.audio_decision``) is advisory. This
module turns its outcome into a typed route; the hub's own policy decides what may happen:

* ``ok=False`` (unavailable, unauthorized, queue_full, deadline_exceeded, ...) ->
  ``NO_DECISION``: the hub takes its normal path.
* field ``ok`` + ``calibrated`` + confidence -> ``PROPOSAL``: a proposed value only; the
  hub policy checks permission and may require a confirmation.
* field ``ok`` without calibration -> ``RANKING``: an unverified ordering; the hub may at
  most ask for a confirmation, never act directly on it.
* ``abstain``/``ambiguous``/``unsupported`` -> ``NO_VALUE``.
* a fallback transcript with ``system2_required`` -> ``SYSTEM2`` for the hub's System-2 path.

A decision value never grants a permission; errors, timeouts and abstentions never become
an implicit allow or a default label.

Stream sessions (``gate_stream_event``): only a non-debounced ``final`` event reaches the gate;
``partial`` results are provisional, and ``speech_start``/``revoked``/``dropped`` carry no value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from voice_runtime.backends.audio_decision import DecisionOutcome, FieldOutcome, StreamEvent


class AudioDecisionKind(str, Enum):
    NO_DECISION = "no_decision"
    PROPOSAL = "proposal"
    RANKING = "ranking"
    NO_VALUE = "no_value"
    SYSTEM2 = "system2"


class HubAction(str, Enum):
    NORMAL_PATH = "normal_path"  # provider gave nothing usable; hub's regular pipeline
    SYSTEM2 = "system2"  # transcript goes to the hub's System-2 step
    ASK_AGAIN = "ask_again"  # no value; re-prompt or fall back
    CONFIRM = "confirm"  # hub policy wants an explicit confirmation first
    ACT = "act"  # hub policy allowed it (never produced by the decision alone)
    DENY = "deny"


class PolicyVerdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class AudioDecisionProposal:
    """What the hub policy sees; carries no permission of its own."""

    field: str
    value: Any
    kind: AudioDecisionKind
    confidence: float | None
    probability: float
    provenance: Mapping[str, Any]
    grants_permission: bool = False


class AudioDecisionPolicy(Protocol):
    def evaluate(self, proposal: AudioDecisionProposal) -> PolicyVerdict: ...


@dataclass(frozen=True)
class HubAudioDecision:
    field: str
    kind: AudioDecisionKind
    action: HubAction
    value: Any = None
    confidence: float | None = None
    reasons: tuple[str, ...] = ()
    error_code: str | None = None
    system2_transcript: str | None = field(default=None, repr=False)
    matched_from_transcript: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    grants_permission: bool = False

    def as_audit_dict(self) -> dict[str, Any]:
        """Audit projection without transcript content."""
        return {
            "field": self.field,
            "kind": self.kind.value,
            "action": self.action.value,
            "has_value": self.value is not None,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "error_code": self.error_code,
            "system2": self.system2_transcript is not None,
            "provenance": dict(self.provenance),
            "grants_permission": False,
        }


def classify_field(outcome: DecisionOutcome, name: str) -> AudioDecisionKind:
    if not outcome.ok:
        return AudioDecisionKind.NO_DECISION
    item: FieldOutcome | None = outcome.fields.get(name)
    if item is None or not item.accepted:
        if outcome.system2_required and outcome.transcript:
            return AudioDecisionKind.SYSTEM2
        return AudioDecisionKind.NO_VALUE
    if item.calibrated and item.confidence is not None:
        return AudioDecisionKind.PROPOSAL
    return AudioDecisionKind.RANKING


PolicyLike = AudioDecisionPolicy | Callable[[AudioDecisionProposal], Any]


def _safe_verdict(policy: PolicyLike, proposal: AudioDecisionProposal) -> PolicyVerdict:
    evaluate = getattr(policy, "evaluate", policy)
    try:
        verdict = evaluate(proposal)
    except Exception:
        return PolicyVerdict.DENY
    try:
        return PolicyVerdict(verdict)
    except ValueError:
        return PolicyVerdict.DENY


def gate_audio_decision(
    outcome: DecisionOutcome,
    *,
    field_name: str,
    policy: PolicyLike,
) -> HubAudioDecision:
    """Map one decision field to a hub action. Only the hub policy can yield ``ACT``."""
    provenance = outcome.provenance.as_dict()
    kind = classify_field(outcome, field_name)
    item = outcome.fields.get(field_name)
    reasons = item.reasons if item is not None else ()
    if kind is AudioDecisionKind.NO_DECISION:
        return HubAudioDecision(
            field=field_name,
            kind=kind,
            action=HubAction.NORMAL_PATH,
            error_code=outcome.error_code,
            provenance=provenance,
        )
    if kind is AudioDecisionKind.SYSTEM2:
        return HubAudioDecision(
            field=field_name,
            kind=kind,
            action=HubAction.SYSTEM2,
            reasons=reasons,
            system2_transcript=outcome.transcript,
            matched_from_transcript=item.matched_from_transcript if item is not None else None,
            provenance=provenance,
        )
    if kind is AudioDecisionKind.NO_VALUE:
        return HubAudioDecision(
            field=field_name, kind=kind, action=HubAction.ASK_AGAIN, reasons=reasons, provenance=provenance
        )

    assert item is not None
    proposal = AudioDecisionProposal(
        field=field_name,
        value=item.value,
        kind=kind,
        confidence=item.confidence if kind is AudioDecisionKind.PROPOSAL else None,
        probability=item.probability,
        provenance=provenance,
    )
    verdict = _safe_verdict(policy, proposal)
    if verdict is PolicyVerdict.DENY:
        action = HubAction.DENY
    elif verdict is PolicyVerdict.CONFIRM or kind is AudioDecisionKind.RANKING:
        # A ranking is never a reliable value: at most a confirmation prompt.
        action = HubAction.CONFIRM
    else:
        action = HubAction.ACT
    return HubAudioDecision(
        field=field_name,
        kind=kind,
        action=action,
        value=item.value,
        confidence=proposal.confidence,
        reasons=reasons,
        provenance=provenance,
    )


def gate_stream_event(
    event: StreamEvent,
    *,
    field_name: str,
    policy: PolicyLike,
) -> HubAudioDecision | None:
    """Gate one stream event; ``None`` means "no hub action" (not final, or a debounced repeat)."""
    if not event.is_final or event.debounced:
        return None
    outcome = event.outcome if event.outcome is not None else DecisionOutcome.failure("bad_response")
    return gate_audio_decision(outcome, field_name=field_name, policy=policy)
