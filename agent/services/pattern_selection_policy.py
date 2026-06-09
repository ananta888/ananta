"""LLM pattern-selection policy for the deterministic code-template library.

The policy enforces the "LLM proposes, validator decides" rule:

- LLMs may *propose* a pattern_id from a task-kind-specific allow-list.
- LLMs may NOT invent new pattern definitions or pattern_ids.
- Risky patterns (e.g. ``singleton_guarded``) require an explicit
  ``allow_risky_patterns=True`` opt-in.
- The local validator is authoritative. A proposal that fails
  validation is recorded as ``rejected`` with a ``blocked_reason``;
  the calling planner can then retry, fall back, or open a review
  gate.

The policy is intentionally read-only and stateless — it never
mutates any plan, registry, or execution context. It returns a
small ``PolicyDecision`` dataclass that the caller is free to log
or surface in worker_execution_context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Default allow-list by task_kind. Keep this conservative: the LLM
# can only suggest patterns we have catalogued. Risky patterns live
# in a separate bucket and are off by default.
DEFAULT_ALLOWLIST: dict[str, set[str]] = {
    "coding": {
        "strategy", "state", "command", "observer", "adapter",
        "facade", "factory_method", "builder",
    },
    "refactoring": {
        "strategy", "state", "command", "observer", "adapter",
        "facade", "proxy", "factory_method", "builder",
    },
    "security": {
        "proxy", "adapter", "facade",
    },
    "test_template": {
        "ts.vitest_scaffold", "cli.retry_wrap",
    },
    "tooling": {
        "cli.retry_wrap", "workflow.sequential_emit",
    },
    "control_policy": {
        "java.default_deny_gate",
    },
    "other": set(),
}


# Pattern IDs that carry extra risk (mutates global state, hides
# construction, or implicitly grants privileges). These are off
# unless the caller explicitly opts in.
RISKY_PATTERN_IDS: set[str] = {
    "singleton_guarded",
    "java.default_deny_gate",
}


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of a single policy check.

    Attributes:
        allowed: True if the proposal passes the policy.
        blocked_reason: human-readable reason when ``allowed`` is False.
        pattern_id: the proposed pattern_id (echoed for logging).
        task_kind: the task_kind the proposal was evaluated against.
        risk_level: one of ``low`` / ``medium`` / ``high`` based on
            the pattern_id and the risk allow-list.
        audit: small dict of extra audit fields (allowlist size, etc.).
    """

    allowed: bool
    blocked_reason: Optional[str] = None
    pattern_id: Optional[str] = None
    task_kind: str = "other"
    risk_level: str = "low"
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked_reason": self.blocked_reason,
            "pattern_id": self.pattern_id,
            "task_kind": self.task_kind,
            "risk_level": self.risk_level,
            "audit": dict(self.audit),
        }


class PatternSelectionPolicy:
    """Stateless policy checker for LLM-proposed pattern selections.

    Construct with overrides as needed; the default constructor is
    safe to share across threads (no mutable state).
    """

    def __init__(
        self,
        allowlist: Optional[dict[str, set[str]]] = None,
        risky_pattern_ids: Optional[set[str]] = None,
    ) -> None:
        self._allowlist: dict[str, set[str]] = {
            k: set(v) for k, v in (allowlist or DEFAULT_ALLOWLIST).items()
        }
        self._risky: set[str] = set(risky_pattern_ids or RISKY_PATTERN_IDS)

    # --- public surface -----------------------------------------------

    def decide(
        self,
        *,
        pattern_id: Optional[str],
        task_kind: str = "other",
        allow_risky_patterns: bool = False,
        catalogue_ids: Optional[set[str]] = None,
    ) -> PolicyDecision:
        """Evaluate a single proposal.

        Args:
            pattern_id: the proposed pattern_id, or None to mean
                "the LLM declined to suggest a pattern".
            task_kind: the task_kind the proposal is being made for.
            allow_risky_patterns: explicit opt-in to allow risky
                pattern ids. Default False.
            catalogue_ids: optional set of currently-valid pattern_ids
                (e.g. from ``PatternRegistry.list()``). When supplied,
                unknown ids are rejected. When None, the policy
                only checks the per-task-kind allow-list and lets the
                caller worry about catalogue validation.
        """
        if not pattern_id:
            return PolicyDecision(
                allowed=True,
                blocked_reason=None,
                pattern_id=None,
                task_kind=task_kind,
                risk_level="low",
                audit={"reason": "no_pattern_proposed"},
            )

        audit: dict[str, Any] = {
            "allowlist_size": len(self._allowlist.get(task_kind, set())),
        }

        if catalogue_ids is not None and pattern_id not in catalogue_ids:
            return PolicyDecision(
                allowed=False,
                blocked_reason=f"pattern_id '{pattern_id}' is not in the catalogue",
                pattern_id=pattern_id,
                task_kind=task_kind,
                risk_level="low",
                audit=audit,
            )

        allowed_ids = self._allowlist.get(task_kind, set())
        if pattern_id not in allowed_ids:
            return PolicyDecision(
                allowed=False,
                blocked_reason=(
                    f"pattern_id '{pattern_id}' is not in the default allow-list for "
                    f"task_kind '{task_kind}'"
                ),
                pattern_id=pattern_id,
                task_kind=task_kind,
                risk_level="low",
                audit=audit,
            )

        if pattern_id in self._risky and not allow_risky_patterns:
            return PolicyDecision(
                allowed=False,
                blocked_reason=(
                    f"pattern_id '{pattern_id}' is marked risky and requires "
                    "allow_risky_patterns=True"
                ),
                pattern_id=pattern_id,
                task_kind=task_kind,
                risk_level="high",
                audit=audit,
            )

        risk_level = "high" if pattern_id in self._risky else "medium"
        return PolicyDecision(
            allowed=True,
            blocked_reason=None,
            pattern_id=pattern_id,
            task_kind=task_kind,
            risk_level=risk_level,
            audit=audit,
        )

    # --- introspection (for tests) ------------------------------------

    def allowlist(self) -> dict[str, frozenset[str]]:
        return {k: frozenset(v) for k, v in self._allowlist.items()}

    def risky_pattern_ids(self) -> frozenset[str]:
        return frozenset(self._risky)


_default_policy: Optional[PatternSelectionPolicy] = None


def get_pattern_selection_policy() -> PatternSelectionPolicy:
    """Return the default policy singleton (stateless, safe to share)."""
    global _default_policy
    if _default_policy is None:
        _default_policy = PatternSelectionPolicy()
    return _default_policy
