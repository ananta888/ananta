"""Normalizer for optional ``pattern_plan`` in worker proposals.

The Ananta hub is the only owner of execution flow. This module is
the *advisory* layer that lets a worker (LLM or rule-based) attach
a deterministic code-template pattern to its proposal, and lets the
hub decide whether to honour it.

Design contract (PAT-008):

- A proposal without ``pattern_plan`` is treated as before: no
  changes, no errors, no new validation. The existing
  ``WorkerContractService`` keeps working untouched.
- A proposal with a *valid* ``pattern_plan`` is stored under
  ``metadata.pattern_plan_normalized`` for downstream rendering
  and audit.
- A proposal with an *invalid* ``pattern_plan`` is *not* executed
  in pattern-rendering mode. The normalizer returns
  ``{"accepted": False, "blocked_reason": "..."}`` so the caller
  can route the task to a retry or to a review gate. The proposal
  itself is still usable for the legacy code path.
- The normalizer never mutates the input proposal; it returns a
  new dict and a side-effect-free ``PatternProposal`` dataclass.

The class is intentionally thin: the heavy validation lives in
``PatternSelectionPolicy`` and ``PatternTemplateRenderer``. This
file is the glue that converts worker-proposal shape into
renderer-input shape and records the policy decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Fields a worker is allowed to put into a pattern_plan dict.
# Anything else is stripped (default-deny on extra keys keeps the
# audit log clean and prevents LLM creativity from leaking into
# the renderer).
_ALLOWED_PATTERN_PLAN_FIELDS: frozenset[str] = frozenset(
    {"pattern_id", "language", "parameters_provided", "task_kind", "allow_risky"}
)


@dataclass(frozen=True)
class PatternProposal:
    """Normalised, validated view of a worker-proposed pattern plan.

    Attributes:
        accepted: True if the proposal passes the policy and is
            safe to hand to the renderer.
        pattern_id: the proposed pattern_id, or None.
        task_kind: the proposed task_kind, or None.
        language: the proposed language tag, or None.
        parameters_provided: a flat dict of substitution values.
        blocked_reason: human-readable reason when accepted=False.
        risk_level: low / medium / high (echoed from the policy).
        audit: small dict of extra fields for the audit log.
    """

    accepted: bool
    pattern_id: Optional[str] = None
    task_kind: Optional[str] = None
    language: Optional[str] = None
    parameters_provided: dict[str, Any] = field(default_factory=dict)
    blocked_reason: Optional[str] = None
    risk_level: str = "low"
    audit: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Render the result as a ``metadata.pattern_plan_normalized``
        block, suitable for the worker_execution_context."""
        return {
            "accepted": self.accepted,
            "pattern_id": self.pattern_id,
            "task_kind": self.task_kind,
            "language": self.language,
            "parameters_provided": dict(self.parameters_provided),
            "blocked_reason": self.blocked_reason,
            "risk_level": self.risk_level,
            "audit": dict(self.audit),
        }


class PatternProposalNormalizer:
    """Stateless normalizer. Safe to share across threads."""

    def __init__(self, policy=None) -> None:
        # Lazy import to avoid a hard dependency between this module
        # and the selection policy. The default policy is fine for
        # most callers; tests may inject a permissive one.
        if policy is None:
            from agent.services.pattern_selection_policy import (
                get_pattern_selection_policy,
            )

            policy = get_pattern_selection_policy()
        self._policy = policy

    def normalize(
        self,
        *,
        proposal: dict[str, Any] | None,
        catalogue_ids: Optional[set[str]] = None,
    ) -> PatternProposal:
        """Normalize an optional pattern_plan from a worker proposal.

        Args:
            proposal: the raw ``pattern_plan`` dict from the
                proposal metadata, or None when the LLM did not
                propose a pattern.
            catalogue_ids: optional set of currently-valid
                pattern_ids (from the registry). When supplied,
                the normalizer uses it for catalogue validation.

        Returns:
            A :class:`PatternProposal`. ``accepted`` is True only
            when the proposal passed both the structural
            validation and the policy gate. Otherwise, the caller
            decides whether to surface the block in the worker
            response or to silently fall back to the legacy path.
        """
        if not proposal:
            return PatternProposal(
                accepted=True,
                audit={"reason": "no_pattern_proposed"},
            )

        if not isinstance(proposal, dict):
            return PatternProposal(
                accepted=False,
                blocked_reason="pattern_plan must be a dict",
                audit={"shape": type(proposal).__name__},
            )

        # Strip unknown keys (default-deny on extra fields).
        clean: dict[str, Any] = {
            k: v for k, v in proposal.items() if k in _ALLOWED_PATTERN_PLAN_FIELDS
        }
        dropped = sorted(set(proposal) - set(clean))
        audit: dict[str, Any] = {"dropped_keys": dropped}

        pattern_id = clean.get("pattern_id")
        task_kind = clean.get("task_kind") or "other"
        language = clean.get("language")
        parameters_provided = clean.get("parameters_provided") or {}
        allow_risky = bool(clean.get("allow_risky"))

        if not isinstance(pattern_id, str) or not pattern_id.strip():
            return PatternProposal(
                accepted=False,
                blocked_reason="pattern_id is required and must be a non-empty string",
                task_kind=str(task_kind),
                language=str(language) if language else None,
                audit=audit,
            )

        if not isinstance(parameters_provided, dict):
            return PatternProposal(
                accepted=False,
                blocked_reason="parameters_provided must be a dict",
                pattern_id=pattern_id,
                task_kind=str(task_kind),
                language=str(language) if language else None,
                audit=audit,
            )

        # Defer the policy decision. Catalogue_ids are threaded
        # through so a stale proposal can be rejected immediately.
        decision = self._policy.decide(
            pattern_id=pattern_id,
            task_kind=str(task_kind),
            allow_risky_patterns=allow_risky,
            catalogue_ids=catalogue_ids,
        )
        audit["policy_audit"] = decision.audit

        return PatternProposal(
            accepted=decision.allowed,
            pattern_id=pattern_id,
            task_kind=str(task_kind),
            language=str(language) if language else None,
            parameters_provided=dict(parameters_provided),
            blocked_reason=decision.blocked_reason,
            risk_level=decision.risk_level,
            audit=audit,
        )


_default_normalizer: Optional[PatternProposalNormalizer] = None


def get_pattern_proposal_normalizer() -> PatternProposalNormalizer:
    """Return the shared normalizer (stateless, safe to share)."""
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = PatternProposalNormalizer()
    return _default_normalizer
