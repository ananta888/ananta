"""Context budget of the ananta-worker loops: every iteration prompt fits the window (LCTX-012).

The worker gets its task and context bundle from the Hub and then iterates: the
tool loop adds tool results, the batch loop adds progress notes, the mutation
loop adds feedback evidence. Those growing parts get exactly the room the window
leaves after the fixed parts (task, instructions, current batch) and a reserve
for the answer:

    available = window - answer reserve - fixed parts

The newest entries stay complete; older ones are condensed (a heading and their
beginning); if even that does not fit, the oldest are left out with a note. Every
shortening is recorded (``agent.context_window``), never silent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from agent.context_profile import ContextBudgets
from agent.context_window import CHARS_PER_TOKEN, context_window_tokens, estimate_tokens, record_truncation

ANSWER_RESERVE_TOKENS = 2048  # compatibility: the output reserve of the 32k profile
CONDENSED_CHARS = 400


def available_chars(*fixed_parts: str, reserve_tokens: int | None = None, cap_chars: int | None = None,
                    window_tokens: int | None = None) -> int:
    """Characters left for the growing parts of a prompt: the effective window minus the answer reserve and the
    safety margin of the central policy (or ``reserve_tokens``) and the ``fixed_parts``."""
    window = int(window_tokens or context_window_tokens())
    budgets = ContextBudgets(window)
    reserve = budgets.output_reserve + budgets.safety_margin if reserve_tokens is None else int(reserve_tokens)
    fixed = sum(estimate_tokens(part) for part in fixed_parts if part)
    remaining = max(0, (window - reserve - fixed) * CHARS_PER_TOKEN)
    return min(remaining, int(cap_chars)) if cap_chars else remaining


def condense_text(block: str, keep_chars: int = CONDENSED_CHARS) -> str:
    """The first line (usually a heading) and the beginning of ``block``, marked as condensed."""
    text = str(block or "")
    if len(text) <= keep_chars:
        return text
    return text[:keep_chars].rstrip() + "\n…[verdichtet: gekürzt, um im Kontextfenster zu bleiben]"


def _left_out_notice(count: int) -> str:
    return f"[{count} ältere Einträge ausgelassen, um im Kontextfenster zu bleiben]"


def fit_blocks(blocks: Sequence[str], budget_chars: int, *, site: str,
               condense: Callable[[str], str] = condense_text, **detail: object) -> list[str]:
    """``blocks`` (oldest first) within ``budget_chars``: newest complete, older condensed, oldest left out.
    The notice about left-out blocks counts against the budget too."""
    remaining = int(budget_chars)
    if sum(len(block) for block in blocks) > remaining:
        remaining -= len(_left_out_notice(999))
    kept: list[str] = []
    condensed = dropped = 0
    for block in reversed(list(blocks)):
        if len(block) <= remaining:
            kept.append(block)
        else:
            short = condense(block)
            if len(short) <= remaining:
                kept.append(short)
                condensed += 1
            else:
                dropped += 1
                continue
        remaining -= len(kept[-1])
    kept.reverse()
    if dropped:
        kept.insert(0, _left_out_notice(dropped))
    if condensed or dropped:
        before = sum(len(block) for block in blocks) // CHARS_PER_TOKEN
        after = sum(len(block) for block in kept) // CHARS_PER_TOKEN
        record_truncation(site, "condense", before_tokens=before, after_tokens=after,
                          dropped_items=condensed + dropped, condensed=condensed, left_out=dropped, **detail)
    return kept
