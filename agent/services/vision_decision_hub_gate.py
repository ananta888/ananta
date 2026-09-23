"""Hub-side mapping of VisionDecision outcomes onto typed hub actions.

The VisionDecision provider (``agent.services.vision_decision_provider``) is advisory. This module
turns one context of its outcome into typed per-field decisions; the hub's own policy decides what
may happen with a value:

* ``ok=False`` (unavailable, timeout, HTTP 4xx/5xx, bad response, ...) -> ``NO_DECISION``: the hub
  takes its normal path. No field gets a value, never a default label, never an implicit allow.
* field abstained (server ``abstained`` list, ``abstain: true`` or Ananta's local threshold check)
  -> ``ESCALATE`` with a typed :class:`VisionEscalation` (larger model, chat completion or human).
  Schema-valid is not correct: an abstained value is never taken over.
* field accepted -> ``PROPOSAL``: the hub policy decides ``ACT``/``CONFIRM``/``DENY``; without a
  policy the result is ``CONFIRM``. A policy error denies.

A decision value never grants a permission: ``grants_permission`` is always False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol

from agent.services.vision_decision_provider import VisionDecisionOutcome, VisionFieldOutcome


class VisionDecisionKind(str, Enum):
    NO_DECISION = "no_decision"
    PROPOSAL = "proposal"
    ESCALATE = "escalate"


class VisionHubAction(str, Enum):
    NORMAL_PATH = "normal_path"  # provider gave nothing usable; hub's regular pipeline
    ESCALATE = "escalate"  # uncertain field: ask a larger model, a chat completion or a human
    CONFIRM = "confirm"  # hub policy wants an explicit confirmation first
    ACT = "act"  # hub policy allowed it (never produced by the decision alone)
    DENY = "deny"


class EscalationTarget(str, Enum):
    LARGER_MODEL = "larger_model"
    CHAT_COMPLETION = "chat_completion"  # normal VLM chat with json_schema on the same image
    HUMAN = "human"


class VisionPolicyVerdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class VisionEscalation:
    field: str
    target: EscalationTarget
    reasons: tuple[str, ...]
    probability: float
    margin: float | None
    entropy: float | None
    # The server's top value, only as context for the escalation target; never a decision.
    candidate: Any = field(default=None, repr=False)

    def as_dict(self, *, with_candidate: bool = False) -> dict[str, Any]:
        out = {
            "field": self.field,
            "target": self.target.value,
            "reasons": list(self.reasons),
            "probability": self.probability,
            "margin": self.margin,
            "entropy": self.entropy,
        }
        if with_candidate:
            out["candidate"] = self.candidate
        return out


@dataclass(frozen=True)
class VisionProposal:
    """What the hub policy sees; carries no permission of its own."""

    schema_id: str | None
    field: str
    value: Any
    probability: float
    margin: float | None
    entropy: float | None
    provenance: Mapping[str, Any]
    grants_permission: bool = False


class VisionDecisionPolicy(Protocol):
    def evaluate(self, proposal: VisionProposal) -> VisionPolicyVerdict: ...


PolicyLike = VisionDecisionPolicy | Callable[[VisionProposal], Any]


@dataclass(frozen=True)
class VisionFieldDecision:
    field: str
    kind: VisionDecisionKind
    action: VisionHubAction
    value: Any = None  # only for PROPOSAL; None for ESCALATE and NO_DECISION
    probability: float | None = None
    margin: float | None = None
    entropy: float | None = None
    abstain: bool | None = None
    escalation: VisionEscalation | None = None
    grants_permission: bool = False

    def as_audit_dict(self) -> dict[str, Any]:
        """No value, no label."""
        return {
            "field": self.field,
            "kind": self.kind.value,
            "action": self.action.value,
            "has_value": self.value is not None,
            "probability": self.probability,
            "margin": self.margin,
            "entropy": self.entropy,
            "abstain": self.abstain,
            "escalation": self.escalation.as_dict() if self.escalation is not None else None,
            "grants_permission": False,
        }


@dataclass(frozen=True)
class HubVisionDecision:
    context_index: int
    kind: VisionDecisionKind
    fields: Mapping[str, VisionFieldDecision] = field(default_factory=dict)
    error_code: str | None = None
    http_status: int | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    grants_permission: bool = False

    @property
    def escalations(self) -> tuple[VisionEscalation, ...]:
        return tuple(d.escalation for d in self.fields.values() if d.escalation is not None)

    @property
    def actionable(self) -> dict[str, Any]:
        """Values the hub policy allowed; still no permission of their own."""
        return {name: d.value for name, d in self.fields.items() if d.action is VisionHubAction.ACT}

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "context_index": self.context_index,
            "kind": self.kind.value,
            "error_code": self.error_code,
            "http_status": self.http_status,
            "fields": [d.as_audit_dict() for d in self.fields.values()],
            "provenance": dict(self.provenance),
            "grants_permission": False,
        }


def _safe_verdict(policy: PolicyLike | None, proposal: VisionProposal) -> VisionPolicyVerdict:
    if policy is None:
        return VisionPolicyVerdict.CONFIRM
    evaluate = getattr(policy, "evaluate", policy)
    try:
        verdict = evaluate(proposal)
    except Exception:
        return VisionPolicyVerdict.DENY
    try:
        return VisionPolicyVerdict(verdict)
    except ValueError:
        return VisionPolicyVerdict.DENY


def _field_decision(
    item: VisionFieldOutcome,
    *,
    schema_id: str | None,
    provenance: Mapping[str, Any],
    policy: PolicyLike | None,
    target: EscalationTarget,
) -> VisionFieldDecision:
    common = {
        "field": item.name,
        "probability": item.probability,
        "margin": item.margin,
        "entropy": item.entropy,
        "abstain": item.abstain,
    }
    if not item.accepted:
        escalation = VisionEscalation(
            field=item.name,
            target=target,
            reasons=item.abstain_reasons or ("abstain",),
            probability=item.probability,
            margin=item.margin,
            entropy=item.entropy,
            candidate=item.top_value,
        )
        return VisionFieldDecision(
            kind=VisionDecisionKind.ESCALATE, action=VisionHubAction.ESCALATE, escalation=escalation, **common
        )
    proposal = VisionProposal(
        schema_id=schema_id,
        field=item.name,
        value=item.value,
        probability=item.probability,
        margin=item.margin,
        entropy=item.entropy,
        provenance=provenance,
    )
    verdict = _safe_verdict(policy, proposal)
    action = {
        VisionPolicyVerdict.ALLOW: VisionHubAction.ACT,
        VisionPolicyVerdict.CONFIRM: VisionHubAction.CONFIRM,
    }.get(verdict, VisionHubAction.DENY)
    return VisionFieldDecision(
        kind=VisionDecisionKind.PROPOSAL,
        action=action,
        value=item.value if action is not VisionHubAction.DENY else None,
        **common,
    )


def gate_vision_decision(
    outcome: VisionDecisionOutcome,
    *,
    context_index: int = 0,
    policy: PolicyLike | None = None,
    escalation_target: EscalationTarget = EscalationTarget.CHAT_COMPLETION,
    human_review_fields: Iterable[str] = (),
) -> HubVisionDecision:
    """Map one context of an outcome to typed field decisions. Only the hub policy can yield ``ACT``."""
    provenance = outcome.provenance()
    result = next((r for r in outcome.results if r.index == context_index), None) if outcome.ok else None
    if result is None:
        return HubVisionDecision(
            context_index=context_index,
            kind=VisionDecisionKind.NO_DECISION,
            error_code=outcome.error_code if not outcome.ok else "missing_context",
            http_status=outcome.http_status,
            provenance=provenance,
        )
    human = frozenset(human_review_fields)
    decisions = {
        name: _field_decision(
            item,
            schema_id=outcome.schema_id,
            provenance=provenance,
            policy=policy,
            target=EscalationTarget.HUMAN if name in human else escalation_target,
        )
        for name, item in result.fields.items()
    }
    escalated = any(d.kind is VisionDecisionKind.ESCALATE for d in decisions.values())
    return HubVisionDecision(
        context_index=context_index,
        kind=VisionDecisionKind.ESCALATE if escalated else VisionDecisionKind.PROPOSAL,
        fields=decisions,
        http_status=outcome.http_status,
        provenance=provenance,
    )
