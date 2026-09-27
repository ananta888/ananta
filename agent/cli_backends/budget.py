"""Token-budget adapter for CLI backend command execution."""
from __future__ import annotations

import logging
from typing import Any

from agent.cli_backends.context import default_context

log = logging.getLogger(__name__)

BudgetError = tuple[int, str, str]


_BACKEND_DEFAULTS = {"opencode": 128000}


def prompt_token_limit(backend: str) -> int:
    """The prompt-token gate of a CLI backend: ``MAX_PROMPT_TOKENS``, else the backend's default, else the
    Ananta context window (``ANANTA_CONTEXT_TOKENS``, 32k)."""
    from agent.config import settings

    explicit = getattr(settings, "max_prompt_tokens", None)
    if explicit:
        return int(explicit)
    return int(_BACKEND_DEFAULTS.get(backend) or getattr(settings, "default_context_tokens", 32768) or 32768)


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
