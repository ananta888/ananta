"""Chat memory: configurable recent turns, rolling summary, and context assembly.

Replaces the hardcoded _extract_prior_messages (last 6, 300 chars each) with a
configurable two-layer memory model:
  - recent_turns: last N user/assistant messages, bounded by turns and chars
  - rolling_summary: compact summary of older turns, updated after each answer
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

_SUMMARY_KEY = "chat_memory_summary"
_SUMMARY_TURN_COUNT_KEY = "chat_memory_summary_turn_count"


@dataclass
class MemoryTurn:
    role: str       # "user" | "assistant"
    content: str


@dataclass
class ChatMemoryContext:
    recent_turns: list[MemoryTurn]
    rolling_summary: str = ""
    active_target_excerpt: str = ""
    codecompass_refs: list[str] = field(default_factory=list)
    rag_snippets: list[str] = field(default_factory=list)
    runtime_status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prior_messages(self) -> list[dict[str, str]]:
        """OpenAI-compatible message dicts for recent turns."""
        return [{"role": t.role, "content": t.content} for t in self.recent_turns]

    def serializable(self) -> dict[str, Any]:
        return {
            "recent_turns": [{"role": t.role, "content": t.content} for t in self.recent_turns],
            "rolling_summary": self.rolling_summary,
            "active_target_excerpt": self.active_target_excerpt[:200] if self.active_target_excerpt else "",
            "codecompass_ref_count": len(self.codecompass_refs),
            "rag_snippet_count": len(self.rag_snippets),
            "runtime_status": self.runtime_status,
            "metadata": self.metadata,
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "recent_turns": len(self.recent_turns),
            "rolling_summary_chars": len(self.rolling_summary),
            "active_target_chars": len(self.active_target_excerpt),
            "codecompass_refs": len(self.codecompass_refs),
            "rag_snippets": len(self.rag_snippets),
            "runtime_status": bool(self.runtime_status),
            **self.metadata,
        }


def extract_memory_context(
    game: dict[str, Any],
    *,
    current_question: str = "",
    max_turns: int = 6,
    max_chars: int = 1800,
    include_control: bool = False,
) -> ChatMemoryContext:
    """Build ChatMemoryContext from current game/chat state."""
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state
        chat = get_chat_state(game)
        ai_ch = (chat.get("channels") or {}).get("ai:tutor") or {}
        all_msgs = [m for m in (ai_ch.get("messages") or []) if isinstance(m, dict)]

        current_norm = " ".join(str(current_question or "").split()).lower()
        turns: list[MemoryTurn] = []
        for m in all_msgs:
            kind = str(m.get("sender_kind") or "")
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            if kind == "user" and current_norm and " ".join(text.split()).lower() == current_norm:
                continue
            if kind not in {"user", "ai"} and not include_control:
                continue
            role = "assistant" if kind == "ai" else "user"
            turns.append(MemoryTurn(role=role, content=text))

        # Apply turn limit; max_turns=0 means disabled → empty
        if max_turns <= 0:
            return ChatMemoryContext(recent_turns=[], metadata={"turns_available": len(turns), "turns_kept": 0, "chars_used": 0})
        recent = turns[-max_turns:]

        # Apply char budget: trim from oldest until within budget
        char_budget = max(100, max_chars)
        used = 0
        kept: list[MemoryTurn] = []
        for turn in reversed(recent):
            cost = len(turn.content)
            if used + cost > char_budget and kept:
                break
            kept.insert(0, turn)
            used += cost

        return ChatMemoryContext(
            recent_turns=kept,
            rolling_summary=get_rolling_summary(game),
            metadata={
                "turns_available": len(turns),
                "turns_kept": len(kept),
                "chars_used": used,
            },
        )
    except Exception:
        return ChatMemoryContext(recent_turns=[])


def get_rolling_summary(game: dict[str, Any]) -> str:
    return str(game.get(_SUMMARY_KEY) or "")


def set_rolling_summary(game: dict[str, Any], summary: str) -> None:
    game[_SUMMARY_KEY] = summary


def update_rolling_summary(
    game: dict[str, Any],
    *,
    last_question: str,
    last_answer: str,
    max_chars: int = 1500,
    update_every_turns: int = 1,
) -> str:
    """Append compact Q→A entry and trim to max_chars. Returns new summary."""
    turn_count = int(game.get(_SUMMARY_TURN_COUNT_KEY) or 0) + 1
    game[_SUMMARY_TURN_COUNT_KEY] = turn_count

    if update_every_turns > 1 and turn_count % update_every_turns != 0:
        return get_rolling_summary(game)

    q_abbrev = last_question[:120].replace("\n", " ").strip()
    a_abbrev = last_answer[:200].replace("\n", " ").strip()
    entry = f"Q: {q_abbrev} → A: {a_abbrev}"

    existing = get_rolling_summary(game)
    if existing:
        combined = existing + "\n" + entry
    else:
        combined = entry

    if len(combined) > max_chars:
        lines = combined.splitlines()
        while len("\n".join(lines)) > max_chars and len(lines) > 1:
            lines.pop(0)
        combined = "\n".join(lines)

    set_rolling_summary(game, combined[:max_chars])
    return combined[:max_chars]


def build_runtime_status(game: dict[str, Any], *, max_chars: int = 300) -> str:
    """Compact runtime context: active view, backend, snake state."""
    parts: list[str] = []
    backend = str(game.get("chat_backend") or "").strip()
    if backend:
        parts.append(f"backend={backend}")
    active_view = str(game.get("visual_viewport_active_view") or "").strip()
    if active_view:
        parts.append(f"view={active_view}")
    snake_mode = bool(game.get("header_logo_game_snake_mode_active") if isinstance(game.get("header_logo_game_snake_mode_active"), bool) else False)
    if snake_mode:
        parts.append("mode=snake")
    overlay = bool(game.get("visual_view_switcher_overlay_visible"))
    if overlay:
        parts.append("overlay=visible")
    return ("[TUI-Status] " + " | ".join(parts))[:max_chars] if parts else ""


def resolve_memory_settings(game: dict[str, Any]) -> dict[str, Any]:
    """Extract all memory-related settings from game state with defaults."""
    def _bool(key: str, default: bool) -> bool:
        v = game.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in {"1", "true", "yes", "an"}
        return default

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(game.get(key) or default)))
        except (TypeError, ValueError):
            return default

    return {
        "use_history": _bool("chat_use_history", True),
        "history_turns": _int("chat_history_turns", 6, 1, 30),
        "history_chars": _int("chat_history_chars", 1800, 100, 10000),
        "use_summary": _bool("chat_use_summary", True),
        "summary_chars": _int("chat_summary_chars", 1500, 100, 5000),
        "summary_update_every_turns": _int("chat_summary_update_every_turns", 3, 1, 20),
        "pass_memory_to_worker": _bool("chat_pass_memory_to_worker", True),
        "worker_mode": str(game.get("chat_worker_mode") or "snake_ask").strip(),
        "backend_fallback": str(game.get("chat_backend_fallback") or "lmstudio").strip(),
        "include_runtime_status": _bool("chat_include_runtime_status", False),
    }
