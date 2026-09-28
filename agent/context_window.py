"""Context window fit and truncation accounting (LCTX-002).

Ananta works with a context window per request that is one central profile (``agent.context_profile``:
``ANANTA_CONTEXT_PROFILE``, default 32k; ``ANANTA_CONTEXT_TOKENS``), capped by provider/model limits.
Two things every call site needs, in one place:

- ``check_fit``: does the assembled prompt fit the window minus the reserve
  for the answer? (Estimated like the trimming code estimates: ~4 chars per
  token, so the check and the trim agree.)
- ``record_truncation``: whenever context is shortened -- messages trimmed,
  text cut, tool results condensed, chunks dropped -- the event is logged,
  counted (``context_truncation_total``) and collected for the current call, so
  a result can say that and how much was lost. Nothing is shortened silently.

Collection is scoped with ``truncation_scope()`` (a context variable), so a
caller such as ``generate_text`` can attach exactly its own events to its
result. Recording never raises and never changes what is sent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

CHARS_PER_TOKEN = 4
DEFAULT_OUTPUT_RESERVE = 1024  # compatibility constant; the reserve of a window: output_reserve_tokens()
_log = logging.getLogger("ananta.context_window")
_EVENTS: ContextVar[list[TruncationEvent] | None] = ContextVar("context_truncations", default=None)
# bounded label values for metrics (free-form sites are folded into "other")
KNOWN_SITES = frozenset({"llm.trim_messages", "llm.lmstudio_completion", "llm.generate_text", "tool_loop.results",
                         "planning.context", "recovery.context", "recovery.goal", "context_bundle", "rag.rerank",
                         "context_compression", "rlm.evidence"})
KINDS = frozenset({"trim_messages", "char_cut", "condense", "drop_items"})


def estimate_tokens(text: str | None) -> int:
    """Rough token count, consistent with the trimming code (``LLMStrategy._estimate_tokens``)."""
    return (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def estimate_messages_tokens(messages: Sequence[Any] | None) -> int:
    total = 0
    for message in messages or ():
        content = message.get("content", "") if isinstance(message, dict) else message
        total += estimate_tokens(content if isinstance(content, str) else str(content))
    return total


def context_window_tokens() -> int:
    """The effective context window (configured profile capped by model/provider limits)."""
    from agent.context_profile import effective_window_tokens

    return effective_window_tokens()


def output_reserve_tokens(window_tokens: int | None = None) -> int:
    """Room for the answer in ``window_tokens`` (central policy: 1/16 of the window, 1024..8192)."""
    from agent.context_profile import ContextBudgets

    return ContextBudgets(int(window_tokens or context_window_tokens())).output_reserve


_central_output_reserve = output_reserve_tokens  # check_fit's parameter shadows the name


@dataclass(frozen=True)
class ContextFit:
    window_tokens: int
    output_reserve_tokens: int
    estimated_tokens: int

    @property
    def budget_tokens(self) -> int:
        return max(0, self.window_tokens - self.output_reserve_tokens)

    @property
    def fits(self) -> bool:
        return self.estimated_tokens <= self.budget_tokens

    @property
    def overflow_tokens(self) -> int:
        return max(0, self.estimated_tokens - self.budget_tokens)

    @property
    def ratio(self) -> float:
        """Estimated size relative to the budget (> 1 does not fit)."""
        return self.estimated_tokens / self.budget_tokens if self.budget_tokens else float("inf")

    def to_mapping(self) -> dict[str, Any]:
        return {"window_tokens": self.window_tokens, "output_reserve_tokens": self.output_reserve_tokens,
                "estimated_tokens": self.estimated_tokens, "fits": self.fits,
                "overflow_tokens": self.overflow_tokens, "ratio": round(self.ratio, 3)}


def check_fit(*, prompt: str = "", messages: Sequence[Any] | None = None, window_tokens: int | None = None,
              output_reserve_tokens: int | None = None) -> ContextFit:
    """Whether ``prompt`` plus ``messages`` fit ``window_tokens`` (default: the effective window) minus the reserve
    (default: the central output reserve of that window)."""
    window = int(window_tokens or context_window_tokens())
    reserve = _central_output_reserve(window) if output_reserve_tokens is None else int(output_reserve_tokens)
    return ContextFit(window, reserve, estimate_tokens(prompt) + estimate_messages_tokens(messages))


@dataclass(frozen=True)
class TruncationEvent:
    site: str  # where, e.g. "llm.trim_messages", "tool_loop.results", "planning.context"
    kind: str  # trim_messages | char_cut | condense | drop_items
    before_tokens: int
    after_tokens: int
    dropped_items: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def lost_tokens(self) -> int:
        return max(0, self.before_tokens - self.after_tokens)

    def to_mapping(self) -> dict[str, Any]:
        return {**asdict(self), "lost_tokens": self.lost_tokens}


def _metric_site(site: str) -> str:
    return site if site in KNOWN_SITES else "other"


def record_truncation(site: str, kind: str, *, before_tokens: int, after_tokens: int, dropped_items: int = 0,
                      **detail: Any) -> TruncationEvent | None:
    """Log, count and collect one shortening of context. ``None`` (nothing recorded) when nothing was lost."""
    if before_tokens <= after_tokens and not dropped_items:
        return None
    event = TruncationEvent(site, kind if kind in KINDS else "char_cut", int(before_tokens), int(after_tokens),
                            int(dropped_items), {k: v for k, v in detail.items() if v is not None})
    try:
        _log.warning("context truncated at %s (%s): ~%d -> ~%d tokens, %d item(s) dropped %s", event.site,
                     event.kind, event.before_tokens, event.after_tokens, event.dropped_items, event.detail or "")
        from agent.metrics import CONTEXT_TRUNCATIONS_TOTAL

        CONTEXT_TRUNCATIONS_TOTAL.labels(site=_metric_site(site), kind=event.kind).inc()
    except Exception:  # noqa: BLE001 -- accounting never breaks a call
        pass
    events = _EVENTS.get()
    if events is not None:
        events.append(event)
    return event


def record_expected_overflow(site: str, fit: ContextFit) -> None:
    """Count a prompt that is estimated above its window before the call (the call site then trims or splits)."""
    if fit.fits:
        return
    try:
        _log.info("context overflow expected at %s: %s", site, fit.to_mapping())
        from agent.metrics import CONTEXT_OVERFLOW_EXPECTED_TOTAL

        CONTEXT_OVERFLOW_EXPECTED_TOTAL.labels(site=_metric_site(site)).inc()
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def truncation_scope() -> Iterator[list[TruncationEvent]]:
    """Collect the truncations recorded inside the block (nested scopes also report to their parents)."""
    parent = _EVENTS.get()
    events: list[TruncationEvent] = []
    token = _EVENTS.set(events)
    try:
        yield events
    finally:
        _EVENTS.reset(token)
        if parent is not None:
            parent.extend(events)


def truncation_summary(events: Sequence[TruncationEvent]) -> dict[str, Any] | None:
    """What a result carries about its shortened context (``None`` when nothing was lost)."""
    if not events:
        return None
    return {"truncated": True, "events": [event.to_mapping() for event in events],
            "lost_tokens": sum(event.lost_tokens for event in events),
            "dropped_items": sum(event.dropped_items for event in events)}
