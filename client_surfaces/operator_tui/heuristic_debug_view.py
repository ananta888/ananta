"""HeuristicDebugView — renders heuristic runtime state for the Operator TUI.

Provides:
  - Status bar indicator string: '[AI]', '[H]', '[~]'
  - Debug panel: heuristic_id, TTL remaining, fallback_reason, last 5 source refs
  - Proposal count for TUI header
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HeuristicDebugState:
    heuristic_id: str | None = None
    version: str | None = None
    source: str = "heuristic"          # ai | heuristic | hybrid
    ttl_remaining_seconds: float | None = None
    fallback_reason: str | None = None
    last_source_refs: list[str] = field(default_factory=list)
    last_action_kind: str | None = None
    open_proposal_count: int = 0
    last_activated_at: float | None = None
    capabilities: list[str] = field(default_factory=list)
    description: str = ""


class HeuristicDebugView:
    """Renders heuristic debug info as plain text lines for the TUI."""

    # ── Status bar ────────────────────────────────────────────────────────────

    @staticmethod
    def status_bar_indicator(state: HeuristicDebugState) -> str:
        """Return '[AI]', '[H]', or '[~]' based on decision source."""
        src = str(state.source or "heuristic").lower()
        if src == "ai":
            return "[AI]"
        if src == "heuristic":
            return "[H]"
        return "[~]"

    # ── Debug panel ───────────────────────────────────────────────────────────

    @staticmethod
    def render_panel(state: HeuristicDebugState) -> str:
        lines: list[str] = ["── Heuristic Debug ──"]

        hid = state.heuristic_id or "(none)"
        ver = f" v{state.version}" if state.version else ""
        lines.append(f"Active: {hid}{ver}")

        if state.ttl_remaining_seconds is not None:
            ttl = max(0.0, state.ttl_remaining_seconds)
            lines.append(f"TTL remaining: {ttl:.1f}s")
        else:
            lines.append("TTL remaining: n/a")

        if state.fallback_reason:
            lines.append(f"Fallback reason: {state.fallback_reason}")

        if state.description:
            lines.append(f"Description: {state.description[:80]}")

        if state.capabilities:
            lines.append(f"Capabilities: {', '.join(state.capabilities)}")

        if state.last_source_refs:
            lines.append("Last source refs:")
            for ref in state.last_source_refs[:5]:
                lines.append(f"  {ref}")

        if state.last_activated_at:
            age = time.time() - state.last_activated_at
            lines.append(f"Activated: {age:.0f}s ago")

        lines.append("─────────────────────")
        return "\n".join(lines)

    # ── Header proposal count ─────────────────────────────────────────────────

    @staticmethod
    def header_proposal_badge(state: HeuristicDebugState) -> str:
        n = state.open_proposal_count
        if n == 0:
            return ""
        return f"[{n} proposal{'s' if n != 1 else ''}]"

    # ── Build from runtime objects ────────────────────────────────────────────

    @staticmethod
    def from_decision_result(
        result: Any,
        *,
        ttl_remaining: float | None = None,
        open_proposal_count: int = 0,
        heuristic_def: Any = None,
    ) -> HeuristicDebugState:
        source_refs = list(getattr(result, "selected_context_refs", None) or [])
        state = HeuristicDebugState(
            heuristic_id=getattr(result, "heuristic_id", None)
                         or (heuristic_def.heuristic_id if heuristic_def else None),
            version=getattr(heuristic_def, "version", None) if heuristic_def else None,
            source=str(getattr(result, "source", "heuristic") or "heuristic"),
            ttl_remaining_seconds=ttl_remaining,
            fallback_reason=getattr(result, "fallback_reason", None),
            last_source_refs=source_refs[:5],
            last_action_kind=getattr(result, "action_kind", None),
            open_proposal_count=open_proposal_count,
            last_activated_at=time.time(),
            capabilities=list(getattr(heuristic_def, "capabilities", None) or []),
            description=getattr(heuristic_def, "description", "") or "",
        )
        return state
