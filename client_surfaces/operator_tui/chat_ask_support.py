"""Pure building blocks of the AI-Snake ``:ask`` resolution.

``ChatMessageFormatterMixin._resolve_ask_question`` orchestrates the ask
flow (prompt building, backend choice, Hub worker call, fallbacks). The
steps without I/O live here so that each one is small and testable on its
own: effective chat settings, retrieval budgets, snippet merging, memory
context defaults, backend resolution, the ``/snake/ask`` v2 payload and the
diagnostics written back into the TUI state.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

SNAKE_ASK_RETRIEVAL_CONFIG_KEYS: tuple[str, ...] = (
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_codecompass_trigger_mode",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_include_task_memory",
    "chat_source_pack_id",
)

DIRECT_LLM_BACKENDS = frozenset({"lmstudio", "local", "openai"})
PROPOSE_WORKER_BACKENDS = frozenset({"opencode", "hermes"})
HUB_WORKER_BACKENDS = frozenset({"ananta-worker", "worker", "hub", "default", "auto"})


def elapsed_ms(t_start: float) -> float:
    return (time.perf_counter() - t_start) * 1000


def apply_effective_chat_settings(game: dict[str, Any], eff_settings: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``game`` overlaid with the non-empty ``chat_*`` session settings."""
    merged = dict(game)
    for key, value in eff_settings.items():
        if key.startswith("chat_") and value is not None and value != "":
            merged[key] = value
    return merged


def resolve_chat_rag_top_k(game: dict[str, Any]) -> int:
    raw = game.get("chat_rag_top_k")
    try:
        top_k = int(raw) if raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_RAG_TOP_K", "24"))
    except (TypeError, ValueError):
        top_k = 24
    return max(8, min(120, top_k))


def resolve_context_budget(game: dict[str, Any]) -> int:
    raw = game.get("chat_context_chars")
    try:
        budget = int(raw) if raw is not None else 3000
    except (TypeError, ValueError):
        budget = 3000
    return max(500, min(20000, budget))


def merge_rag_snippets(question_rag: list[str], rag_context: list[str]) -> list[str]:
    """Question-specific snippets first, de-duplicated by their first 60 characters."""
    seen: set[str] = set()
    merged: list[str] = []
    for item in question_rag + rag_context:
        key = item[:60]
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def ensure_memory_context(
    memory: Any,
    *,
    active_excerpt: str,
    codecompass_refs: list[str],
    rag_snippets: list[str],
) -> Any:
    """The caller's memory context, or a fresh one; fills missing CodeCompass refs."""
    from client_surfaces.operator_tui.chat_memory import ChatMemoryContext

    mem_ctx = memory
    if mem_ctx is None:
        mem_ctx = ChatMemoryContext(
            recent_turns=[],
            rolling_summary="",
            active_target_excerpt=active_excerpt,
            codecompass_refs=codecompass_refs[:8],
            rag_snippets=rag_snippets,
        )
    has_empty_refs = hasattr(mem_ctx, "codecompass_refs") and not mem_ctx.codecompass_refs
    if has_empty_refs and hasattr(mem_ctx, "__dataclass_fields__"):
        object.__setattr__(mem_ctx, "codecompass_refs", codecompass_refs[:8])
    return mem_ctx


def resolve_chat_system_prompt(eff_settings: dict[str, Any]) -> str:
    session_prompt = str(eff_settings.get("chat_system_prompt") or "").strip()
    env_prompt = str(os.environ.get("ANANTA_TUI_CHAT_SYSTEM_PROMPT") or "").strip()
    return session_prompt or env_prompt


def resolve_chat_backend(game: dict[str, Any], endpoint: str) -> str:
    """Configured backend; an LM Studio endpoint forces ``lmstudio`` unless the env pins one."""
    backend = str(game.get("chat_backend") or os.environ.get("ANANTA_TUI_CHAT_BACKEND") or "lmstudio").strip().lower()
    endpoint = str(endpoint or "").strip().lower()
    env_backend = str(os.environ.get("ANANTA_TUI_CHAT_BACKEND") or "").strip().lower()
    if not env_backend and (":1234" in endpoint or "lmstudio" in endpoint):
        return "lmstudio"
    return backend


def _positive_int(game: dict[str, Any], key: str) -> int | None:
    try:
        value = int(game.get(key) or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def build_snake_ask_v2_payload(worker_v2_payload: Any, game: dict[str, Any]) -> dict[str, Any]:
    """The ``/snake/ask`` v2 request body with the TUI chat overrides."""
    payload = dict(worker_v2_payload)
    # Keep repository grounding in the Hub. Sending the TUI-built context
    # would make /snake/ask skip Hub RAG and hide real workspace files from
    # the answer path.
    payload["context"] = ""
    configured_model = str(game.get("chat_backend_model") or "").strip()
    if configured_model:
        payload["model"] = configured_model
    retrieval_config = {
        key: game.get(key)
        for key in SNAKE_ASK_RETRIEVAL_CONFIG_KEYS
        if key in game and isinstance(game.get(key), (str, bool))
    }
    if retrieval_config:
        payload["retrieval_config"] = retrieval_config
    rag_top_k = _positive_int(game, "chat_rag_top_k")
    if rag_top_k is not None:
        payload["rag_top_k"] = max(8, min(120, rag_top_k))
    answer_chars = _positive_int(game, "chat_answer_chars")
    if answer_chars is not None:
        payload["answer_chars"] = answer_chars
    overflow_policy = str(game.get("chat_answer_overflow_policy") or "").strip().lower()
    if overflow_policy in {"allow", "summarize", "truncate"}:
        payload["answer_overflow_policy"] = overflow_policy
    if "chat_never_truncate_answers" in game:
        payload["never_truncate_answers"] = bool(game.get("chat_never_truncate_answers"))
    for game_key, payload_key in (("chat_max_tokens", "max_tokens"), ("chat_context_chars", "context_chars")):
        value = _positive_int(game, game_key)
        if value is not None:
            payload[payload_key] = value
    return payload


@dataclass
class AskDiagnostics:
    """Records which ask path answered, and with which memory, into the TUI state."""

    backend: str
    mem_ctx: Any
    codecompass_refs: list[str]
    rag_snippets: list[str]
    build_result: Any

    def record(self, tui: Any, path: str, latency_ms: float, fallback: str = "") -> None:
        try:
            game = dict(tui.state.header_logo_game or {})
            game["last_chat_backend_used"] = self.backend
            game["last_chat_backend_path"] = path
            game["last_chat_latency_ms"] = round(latency_ms, 1)
            game["last_chat_fallback_reason"] = fallback
            game["last_chat_memory_status"] = {
                "history_used": bool(self.mem_ctx.recent_turns),
                "summary_used": bool(self.mem_ctx.rolling_summary),
                "codecompass_used": bool(self.codecompass_refs),
                "rag_count": len(self.rag_snippets),
                "sections": self.build_result.included_sections,
            }
            tui._set_state(tui.state.with_updates(header_logo_game=game))
        except Exception:
            pass
