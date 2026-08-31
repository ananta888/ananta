"""Pure runtime-selection helpers for OpenCode and Codex CLI backends."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.cli_backends.helpers import (
    _classify_runtime_target,
    _normalize_ollama_openai_base_url,
)


def build_codex_runtime_diagnostics(
    *, base_url: str | None, api_key: str | None, is_local: bool,
) -> list[str]:
    diagnostics: list[str] = []
    if not base_url:
        diagnostics.append("codex_runtime_missing_base_url")
    if not api_key and not is_local:
        diagnostics.append("codex_runtime_missing_api_key_for_remote_target")
    if base_url and _classify_runtime_target(base_url) == "unknown":
        diagnostics.append("codex_runtime_target_host_kind_unknown")
    return diagnostics


def split_cli_model_identifier(model: str | None) -> tuple[str | None, str | None]:
    raw = str(model or "").strip()
    if not raw:
        return None, None
    if "/" not in raw:
        return None, raw
    provider_name, model_name = raw.split("/", 1)
    return provider_name.strip().lower() or None, model_name.strip() or None


def infer_local_opencode_target(
    model: str | None,
    *,
    provider_urls: dict[str, object],
    preferred_provider: str | None,
    timeout: int,
    probe_ollama: Callable[..., Any],
    probe_lmstudio: Callable[..., Any],
    match_ollama: Callable[..., Any],
    match_lmstudio: Callable[..., Any],
    resolve_ollama: Callable[..., Any],
) -> tuple[str | None, str | None]:
    raw_model = str(model or "").strip()
    if not raw_model:
        return None, None
    provider_hint = str(preferred_provider or "").strip().lower()
    candidates = [provider_hint] if provider_hint in {"ollama", "lmstudio"} else []
    candidates.extend(item for item in ("ollama", "lmstudio") if item not in candidates)
    for candidate in candidates:
        if candidate == "ollama":
            base_url = _normalize_ollama_openai_base_url(str(provider_urls.get("ollama") or "").strip())
            if not base_url:
                continue
            try:
                probe = probe_ollama(base_url, timeout=timeout)
            except Exception:
                continue
            matched = match_ollama(raw_model, list(probe.get("models") or [])) if isinstance(probe, dict) else None
            if matched:
                resolved_model = resolve_ollama(raw_model, base_url, timeout=timeout) or raw_model
                return "ollama", str(resolved_model).strip() or raw_model
        else:
            base_url = str(provider_urls.get("lmstudio") or "").strip()
            if not base_url:
                continue
            try:
                probe = probe_lmstudio(base_url, timeout=timeout)
            except Exception:
                continue
            matched = match_lmstudio(raw_model, list(probe.get("candidates") or [])) if isinstance(probe, dict) else None
            if matched:
                return "lmstudio", str((matched or {}).get("id") or "").strip() or raw_model
    return None, None


def build_opencode_runtime_diagnostics(*, base_url: str | None) -> list[str]:
    if not base_url:
        return ["opencode_runtime_missing_base_url"]
    if _classify_runtime_target(base_url) == "unknown":
        return ["opencode_runtime_target_host_kind_unknown"]
    return []


def build_opencode_toolless_agent_config() -> dict[str, object]:
    return {
        "description": "Toolless worker for structured JSON replies",
        "prompt": "Return concise structured answers. Never call tools.",
        "temperature": 0.1,
        "tools": {
            name: False
            for name in (
                "bash", "read", "glob", "grep", "edit", "write", "task",
                "webfetch", "todowrite", "question", "skill",
            )
        },
    }


def normalize_opencode_tool_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"toolless", "readonly", "full"} else "full"


def normalize_opencode_execution_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"backend", "live_terminal", "interactive_terminal"} else "live_terminal"
