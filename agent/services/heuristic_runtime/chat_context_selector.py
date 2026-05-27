"""ChatContextSelector — deterministic context reference ranking and budget limiting.

Priority ranking (highest first):
  1. selected_artifacts from DecisionContext (user explicitly chose)
  2. active_goal / active_task refs
  3. local project sourcepack
  4. helpcenter refs
  5. technical sourcepack
  6. wikipedia

Security-Deny always overrides ranking.
Budget: max 5 refs, max 10 000 token-equivalent (≈ 40 chars/token).
no_good_match returned when no ref with confidence > 0.3 found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.chat_query_classifier import IntentKind
from agent.services.heuristic_runtime.decision_context import DecisionContext

_MAX_REFS = 5
_MAX_TOKEN_EQUIV = 10_000
_CHARS_PER_TOKEN = 40

_SECURITY_DENIED_SCOPES = frozenset({"secret", "credential", "env_var", "private_key"})


@dataclass
class RankedRef:
    ref: str
    confidence: float
    rank: int
    source_tier: str  # artifact | goal | sourcepack | helpcenter | tech | wiki
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "confidence": self.confidence,
            "rank": self.rank,
            "source_tier": self.source_tier,
            "token_estimate": self.token_estimate,
        }


@dataclass
class SelectedContextResult:
    selected_refs: list[str]
    ranked_refs: list[RankedRef]
    budget_used: int  # token-equivalent
    ranking_explanation: str
    is_no_good_match: bool = False
    security_denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_refs": self.selected_refs,
            "budget_used": self.budget_used,
            "ranking_explanation": self.ranking_explanation,
            "is_no_good_match": self.is_no_good_match,
            "security_denied": self.security_denied,
            "ranked_refs": [r.to_dict() for r in self.ranked_refs],
        }


class ChatContextSelector:
    def select(
        self,
        intent: IntentKind,
        context: DecisionContext,
    ) -> SelectedContextResult:
        # Security deny check
        for scope in context.allowed_source_scopes:
            if scope.lower() in _SECURITY_DENIED_SCOPES:
                return SelectedContextResult(
                    selected_refs=[],
                    ranked_refs=[],
                    budget_used=0,
                    ranking_explanation="security_deny:forbidden_scope",
                    is_no_good_match=True,
                    security_denied=True,
                )

        ranked: list[RankedRef] = []
        rank = 0

        # Tier 1 — user-selected artifacts (highest confidence)
        for ref in context.selected_artifacts[:_MAX_REFS]:
            ranked.append(RankedRef(
                ref=ref, confidence=1.0, rank=rank,
                source_tier="artifact",
                token_estimate=_estimate_tokens(ref),
            ))
            rank += 1

        # Tier 2 — active goal / task refs
        if context.active_goal_id:
            ref = f"goal:{context.active_goal_id}"
            ranked.append(RankedRef(ref=ref, confidence=0.85, rank=rank, source_tier="goal",
                                    token_estimate=_estimate_tokens(ref)))
            rank += 1
        if context.active_task_id:
            ref = f"task:{context.active_task_id}"
            ranked.append(RankedRef(ref=ref, confidence=0.8, rank=rank, source_tier="goal",
                                    token_estimate=_estimate_tokens(ref)))
            rank += 1

        # Tier 3 — local project sourcepack (intent-driven)
        if intent in (IntentKind.EXPLAIN_FILE, IntentKind.FIND_SYMBOL, IntentKind.GENERAL_PROJECT_QUESTION):
            ranked.append(RankedRef(ref="sourcepack:local", confidence=0.6, rank=rank,
                                    source_tier="sourcepack", token_estimate=500))
            rank += 1

        # Tier 4 — helpcenter
        if intent in (IntentKind.HELPCENTER_LOOKUP, IntentKind.EXPLAIN_ERROR, IntentKind.TODO_STATUS):
            ranked.append(RankedRef(ref="helpcenter:main", confidence=0.5, rank=rank,
                                    source_tier="helpcenter", token_estimate=200))
            rank += 1

        # Tier 5 — technical sourcepack
        if intent in (IntentKind.FIND_SYMBOL, IntentKind.EXPLAIN_ERROR):
            ranked.append(RankedRef(ref="sourcepack:technical", confidence=0.45, rank=rank,
                                    source_tier="tech", token_estimate=300))
            rank += 1

        # Filter by confidence threshold
        qualified = [r for r in ranked if r.confidence > 0.3]
        if not qualified:
            return SelectedContextResult(
                selected_refs=[], ranked_refs=ranked, budget_used=0,
                ranking_explanation="no_qualified_refs",
                is_no_good_match=True,
            )

        # Budget limiting
        selected: list[RankedRef] = []
        budget = 0
        for ref in qualified:
            if len(selected) >= _MAX_REFS:
                break
            if budget + ref.token_estimate > _MAX_TOKEN_EQUIV:
                break
            selected.append(ref)
            budget += ref.token_estimate

        if not selected:
            return SelectedContextResult(
                selected_refs=[], ranked_refs=ranked, budget_used=0,
                ranking_explanation="budget_exceeded_before_any_ref",
                is_no_good_match=True,
            )

        tiers = [r.source_tier for r in selected]
        explanation = f"selected {len(selected)} refs from tiers: {', '.join(dict.fromkeys(tiers))}"

        return SelectedContextResult(
            selected_refs=[r.ref for r in selected],
            ranked_refs=ranked,
            budget_used=budget,
            ranking_explanation=explanation,
            is_no_good_match=False,
        )


def _estimate_tokens(ref: str) -> int:
    return max(1, len(ref) // _CHARS_PER_TOKEN + 10)
