"""Online context compaction with state invariants (SEH-008).

Compaction drops finished work, redundant observations and old intermediate
steps while **always** preserving open obligations, user constraints, error
states and evidence handles. Every drop is traceable; nothing is silently
forgotten. The invariant holds across arbitrarily many compactions.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

_CHARS_PER_TOKEN = 4

PRESERVED_KINDS = frozenset({"open_obligation", "constraint", "error_state", "evidence_handle", "plan"})
DROPPABLE_KINDS = frozenset({"completed_work", "observation", "intermediate_step"})


def _tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ContextItem:
    item_id: str
    kind: str
    text: str
    token_estimate: int = 0
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.item_id or not isinstance(self.item_id, str):
            raise ValueError("context_item_id_invalid")
        if self.token_estimate == 0:
            object.__setattr__(self, "token_estimate", _tokens(self.text))


@dataclass(frozen=True, slots=True)
class CompactionResult:
    kept: tuple[ContextItem, ...]
    dropped: tuple[str, ...]
    fallback: bool
    reason: str | None
    kept_tokens: int
    original_tokens: int
    trace: dict = field(default_factory=dict)


class ContextCompactor:
    def compact(self, items: Sequence[ContextItem], *, budget_tokens: int,
                droppable_kinds: Iterable[str] = DROPPABLE_KINDS) -> CompactionResult:
        if type(budget_tokens) is not int or budget_tokens < 1:
            raise ValueError("compaction_budget_invalid")
        droppable = frozenset(droppable_kinds)
        items = list(items)
        preserved = [item for item in items if item.kind in PRESERVED_KINDS]
        others = [item for item in items if item.kind not in PRESERVED_KINDS]
        original_tokens = sum(item.token_estimate for item in items)
        preserved_tokens = sum(item.token_estimate for item in preserved)

        # Preserved items are never dropped: if they alone exceed the budget the
        # compactor fails closed (keeps everything) rather than forgetting state.
        if preserved_tokens > budget_tokens:
            return CompactionResult(tuple(items), (), True, "compaction_preserved_exceeds_budget",
                                    original_tokens, original_tokens,
                                    {"preserved": len(preserved), "others": len(others)})

        kept: list[ContextItem] = list(preserved)
        dropped: list[str] = []
        remaining = budget_tokens - preserved_tokens
        # Redundant/old work is removed outright; other items are kept newest-first
        # until the budget is exhausted, then the oldest of them is dropped too.
        redundant = sorted((item for item in others if item.kind in droppable),
                           key=lambda item: (item.created_at, item.item_id))
        dropped.extend(item.item_id for item in redundant)
        keepable = sorted((item for item in others if item.kind not in droppable),
                          key=lambda item: (item.created_at, item.item_id), reverse=True)
        for item in keepable:
            if item.token_estimate <= remaining:
                kept.append(item)
                remaining -= item.token_estimate
            else:
                dropped.append(item.item_id)
        kept.sort(key=lambda item: (item.created_at, item.item_id))
        kept_tokens = sum(item.token_estimate for item in kept)
        return CompactionResult(tuple(kept), tuple(dropped), False, None, kept_tokens, original_tokens,
                                {"preserved": len(preserved), "redundant": len(redundant),
                                 "budget_dropped": len(dropped) - len(redundant)})

    @staticmethod
    def invariant_holds(items: Sequence[ContextItem], result: CompactionResult) -> bool:
        kept_ids = {item.item_id for item in result.kept}
        return all(item.item_id in kept_ids for item in items if item.kind in PRESERVED_KINDS)
