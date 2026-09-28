"""Token-budget adapter for CLI backend command execution."""
from __future__ import annotations

import logging
from typing import Any

from agent.cli_backends.context import default_context

log = logging.getLogger(__name__)

BudgetError = tuple[int, str, str]


_BACKEND_LIMITS = {"opencode": 128000}  # what the backend itself accepts at most


def prompt_token_limit(backend: str) -> int:
    """The prompt-token gate of a CLI backend: the effective context window (``agent.context_profile``),
    narrowed by the backend's own limit and by ``MAX_PROMPT_TOKENS`` -- never above the window."""
    from agent.config import settings
    from agent.context_profile import effective_window_tokens

    explicit = getattr(settings, "max_prompt_tokens", None)
    return effective_window_tokens(limits={"backend": _BACKEND_LIMITS.get(backend),
                                           "max_prompt_tokens": int(explicit) if explicit else None})


def check_prompt_budget(prompt: str, *, max_tokens: Any) -> BudgetError | None:
    """Return a CLI error tuple when the prompt exceeds its token budget."""
    try:
        token_budget_service = default_context.token_budget_service
        estimate = token_budget_service.estimate(prompt)
        decision = token_budget_service.check_budget(
            estimate["tokens"],
            max_tokens=max_tokens,
        )
        if not decision["allowed"]:
            return (
                -1,
                "",
                f"token_budget_exceeded: prompt ~{decision['estimated_tokens']} tokens "
                f"exceeds limit {decision['max_tokens']}",
            )
    except Exception as exc:
        log.debug("Token budget gate skipped (error): %s", exc)
    return None
