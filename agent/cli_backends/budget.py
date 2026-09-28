"""Token-budget adapter for CLI backend command execution."""
from __future__ import annotations

import logging
from typing import Any

from agent.cli_backends.context import default_context

log = logging.getLogger(__name__)

BudgetError = tuple[int, str, str]


def _configured_opencode_model() -> str:
    """The model opencode runs when a call names none (runtime config, then ``OPENCODE_DEFAULT_MODEL``)."""
    from agent.config import settings

    try:
        from flask import current_app, has_app_context

        if has_app_context():
            cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
            runtime = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
            configured = (runtime.get("target_model") or runtime.get("model") or cfg.get("opencode_default_model"))
            if configured:
                return str(configured)
    except Exception:  # noqa: BLE001
        pass
    return str(getattr(settings, "opencode_default_model", "") or "")


# Agent CLIs that always run a subscription/cloud model, and their own prompt limit.
_SUBSCRIPTION_BACKENDS = {"claude": 200_000, "codex": 272_000}
_OPENCODE_DEFAULT_LIMIT = 128_000


def prompt_token_limit(backend: str, *, model: str | None = None, local: bool | None = None) -> int:
    """The prompt-token gate of a CLI backend.

    - ``MAX_PROMPT_TOKENS`` set: that value (a deliberate override for every backend).
    - claude / codex CLI (subscription models) and opencode with a cloud model (``anthropic/...``, ``openai/...``):
      the model's own limit -- the Ananta context profile describes local runtimes and never limits these.
    - opencode with a local model and sgpt (local runtime): the effective Ananta window (at most 128000 for opencode).
    ``local`` (when the caller knows the resolved target, e.g. codex against LM Studio) overrides the guess.
    """
    from agent.config import settings
    from agent.context_profile import CLOUD_PROVIDER_PREFIXES, cloud_model_limit, effective_window_tokens

    explicit = getattr(settings, "max_prompt_tokens", None)
    if explicit:
        return int(explicit)
    name = str(backend or "").strip().lower()
    if local is True:  # e.g. codex/opencode pointed at a local runtime: the Ananta window
        return effective_window_tokens(limits={"backend": _OPENCODE_DEFAULT_LIMIT} if name == "opencode" else None)
    if name in _SUBSCRIPTION_BACKENDS:
        return cloud_model_limit(model, _SUBSCRIPTION_BACKENDS[name]) or _SUBSCRIPTION_BACKENDS[name]
    if name == "opencode":
        model = model or _configured_opencode_model()
        prefix = str(model or "").strip().lower().partition("/")[0] if "/" in str(model or "") else ""
        if prefix in CLOUD_PROVIDER_PREFIXES:
            return cloud_model_limit(model, _OPENCODE_DEFAULT_LIMIT) or _OPENCODE_DEFAULT_LIMIT
        return effective_window_tokens(limits={"backend": _OPENCODE_DEFAULT_LIMIT})
    return effective_window_tokens()


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
