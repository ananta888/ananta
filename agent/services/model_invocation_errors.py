"""Stable error contracts for model invocation failures."""

from __future__ import annotations

from typing import Any


class ModelRoutingConfigurationError(RuntimeError):
    """Explicit model-routing configuration cannot be loaded safely."""


class LLMUnavailableError(Exception):
    """A model attempt failed or returned an unusable contracted response."""

    def __init__(
        self,
        message: str,
        *,
        llm_call_profile: list[dict[str, Any]] | None = None,
        fallback_decisions: list[dict[str, Any]] | None = None,
        terminal_reason: str | None = None,
        model_recovery_signal: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.llm_call_profile = list(llm_call_profile or [])
        self.fallback_decisions = list(fallback_decisions or [])
        self.terminal_reason = str(terminal_reason or "").strip() or self._last_error_type()
        if isinstance(model_recovery_signal, dict):
            self.model_recovery_signal = dict(model_recovery_signal)
        else:
            from ananta_contracts.model_recovery import build_model_recovery_signal

            self.model_recovery_signal = build_model_recovery_signal(
                terminal_reason=self.terminal_reason,
                fallback_decisions=self.fallback_decisions,
                llm_call_profile=self.llm_call_profile,
            )

    def _last_error_type(self) -> str:
        for item in reversed(self.llm_call_profile):
            if isinstance(item, dict):
                value = str(item.get("error_type") or "").strip()
                if value:
                    return value
        return "unknown"
