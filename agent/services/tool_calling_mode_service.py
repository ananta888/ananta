"""UTCR-006: Resolves which tool-calling serialisation mode to use.

Three modes:
- ``native_openai_tools``  — the LLM uses the standard OpenAI tools API
- ``prompt_json_protocol`` — the worker LLM is given tools as prompt text
                             and emits JSON (ananta_worker_tool_loop.v1)
- ``disabled``             — no tools at all

The resolver reads ``config["ananta_worker_tool_calling"]["mode"]``
(default ``"auto"``).  In auto mode it checks against a per-config
``native_backend_allowlist`` (default: openai, lmstudio, ollama, litellm,
openrouter, ananta-worker) and ``native_backend_denylist`` (default:
empty).  Unknown backends fall back to ``prompt_json_protocol``.
"""
from __future__ import annotations

from typing import Any

MODE_NATIVE_OPENAI = "native_openai_tools"
MODE_PROMPT_JSON = "prompt_json_protocol"
MODE_DISABLED = "disabled"

_VALID_EXPLICIT_MODES = {MODE_NATIVE_OPENAI, MODE_PROMPT_JSON, MODE_DISABLED}

_DEFAULT_ALLOWLIST = frozenset(
    {"openai", "lmstudio", "ollama", "litellm", "openrouter", "ananta-worker"}
)


class ToolCallingModeService:
    """Resolves the effective tool-calling mode for a given backend/provider."""

    def resolve_mode(
        self,
        *,
        provider: str = "",
        backend: str = "",
        config: dict[str, Any] | None = None,
    ) -> str:
        """Return the effective tool-calling mode string."""
        cfg = dict(config or {})
        tool_cfg = dict(cfg.get("ananta_worker_tool_calling") or {})
        mode = str(tool_cfg.get("mode") or "auto").strip().lower()

        if mode in _VALID_EXPLICIT_MODES:
            return mode

        # auto resolution
        candidate = (provider or backend).strip().lower()
        allowlist = frozenset(
            str(item).lower()
            for item in tool_cfg.get("native_backend_allowlist") or _DEFAULT_ALLOWLIST
        )
        denylist = frozenset(
            str(item).lower()
            for item in tool_cfg.get("native_backend_denylist") or []
        )
        fallback = str(tool_cfg.get("fallback_mode") or MODE_PROMPT_JSON).strip().lower()
        if fallback not in _VALID_EXPLICIT_MODES:
            fallback = MODE_PROMPT_JSON

        if candidate and candidate in denylist:
            return fallback
        if candidate and candidate in allowlist:
            return MODE_NATIVE_OPENAI
        return fallback

    def is_native_capable(
        self,
        *,
        provider: str = "",
        backend: str = "",
        config: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience predicate: True when the resolved mode is native_openai_tools."""
        return self.resolve_mode(provider=provider, backend=backend, config=config) == MODE_NATIVE_OPENAI


_tool_calling_mode_service = ToolCallingModeService()


def get_tool_calling_mode_service() -> ToolCallingModeService:
    return _tool_calling_mode_service
