"""SnakeHeuristicMixin — heuristic selection and proposal generation for TUI snake.

Evaluates active heuristics from heuristics/active/ against the current screen
snapshot (game state + section + AI status) and applies the best matching action
to the tutorial AI snake.

Periodically calls ProposalService.generate_from_traces() to generate new
heuristic candidates from accumulated decision traces.

All disk I/O (heuristic loading, proposal generation) runs in a background
ThreadPoolExecutor so the 18 TPS main loop is never blocked by file operations.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any


_HEURISTICS_DIR = Path(__file__).resolve().parents[2] / "heuristics" / "active"
_HEURISTIC_CACHE_TTL = 60.0        # reload heuristic files at most once per minute
_PROPOSAL_MIN_TRACES = 25           # generate a proposal after this many decisions
_PROPOSAL_MIN_INTERVAL = 300.0      # at most one proposal every 5 minutes


class SnakeHeuristicMixin:
    """Mixin providing heuristic-based AI snake control and proposal generation."""

    # ── public entry point ────────────────────────────────────────────────────

    def _tick_snake_heuristic(self, game: dict[str, Any], *, now: float) -> None:
        """Select the best matching TUI-snake heuristic and apply its action."""
        if not bool(game.get("tutorial_mode")):
            return

        heuristics = self._load_active_snake_heuristics(now=now)
        if not heuristics:
            return

        section = str(self.state.section_id or "dashboard")
        ai_status = self._snake_ai_status(game)
        artifact_present = isinstance(game.get("artifact_intent_target"), dict)
        context_hash = f"{section}|{ai_status}|{artifact_present}"

        selected: dict[str, Any] | None = None
        for h in heuristics:
            if self._evaluate_heuristic_triggers(h, section=section, ai_status=ai_status, artifact_present=artifact_present):
                selected = h
                break

        if selected is None:
            selected = heuristics[0]   # fallback: first heuristic (follow_with_distance)
            fallback_reason = "no_trigger_match"
        else:
            fallback_reason = ""

        action = selected.get("runtime", {}).get("action") or selected.get("action") or {}
        heuristic_id = str(selected.get("heuristic_id") or "unknown")

        self._apply_heuristic_action(action, game, section=section)
        game["active_heuristic_id"] = heuristic_id
        self._selected_heuristic_id = heuristic_id

        self._record_snake_decision(
            heuristic_id=heuristic_id,
            action_kind=str(action.get("kind") or "unknown"),
            context_hash=context_hash,
            fallback_reason=fallback_reason,
            now=now,
        )
        self._maybe_generate_proposal(now=now)

    # ── heuristic loader (async-safe) ─────────────────────────────────────────

    def _load_active_snake_heuristics(self, *, now: float) -> list[dict[str, Any]]:
        """Return current heuristic list without blocking the main loop.

        Disk I/O runs in a background thread. While loading, the stale cache
        (or empty list) is returned so the tick never waits on file operations.
        """
        cached_at, cached = getattr(self, "_active_heuristics_cache", (0.0, []))

        # Poll background load future
        pending: Future | None = getattr(self, "_heuristic_load_future", None)
        if pending is not None and pending.done():
            try:
                new_result = pending.result()
                cached = new_result
                cached_at = now
                self._active_heuristics_cache = (now, cached)
            except Exception:
                pass
            self._heuristic_load_future = None
            pending = None

        # Cache still fresh
        if cached and (now - cached_at) < _HEURISTIC_CACHE_TTL:
            return cached

        # Cache expired — submit background load if none already pending
        if pending is None:
            executor: ThreadPoolExecutor = getattr(self, "_heuristic_load_executor", None)  # type: ignore[assignment]
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="tui-heuristic-loader"
                )
                self._heuristic_load_executor = executor
            self._heuristic_load_future = executor.submit(self._load_heuristics_from_disk)

        # Return stale data while the background load is in flight
        return cached

    def _load_heuristics_from_disk(self) -> list[dict[str, Any]]:
        """Blocking disk load — always called from background thread."""
        result: list[dict[str, Any]] = []
        if not _HEURISTICS_DIR.is_dir():
            return result

        for path in sorted(_HEURISTICS_DIR.iterdir()):
            if path.suffix != ".json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            domain = str(data.get("domain") or "")
            status = str(data.get("status") or "")
            if domain != "tui_snake" or status != "active":
                continue
            result.append(data)

        # Sort: most specific first (more trigger conditions = more specific)
        result.sort(key=lambda h: -len((h.get("runtime") or {}).get("triggers") or h.get("triggers") or []))
        return result

    # ── trigger evaluation ────────────────────────────────────────────────────

    def _evaluate_heuristic_triggers(
        self,
        heuristic: dict[str, Any],
        *,
        section: str,
        ai_status: str,
        artifact_present: bool,
    ) -> bool:
        triggers = (heuristic.get("runtime") or {}).get("triggers") or heuristic.get("triggers") or []
        if not triggers:
            return True  # unconditional heuristic

        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            if self._trigger_matches(trigger, section=section, ai_status=ai_status, artifact_present=artifact_present):
                return True
        return False

    def _trigger_matches(
        self,
        trigger: dict[str, Any],
        *,
        section: str,
        ai_status: str,
        artifact_present: bool,
    ) -> bool:
        for key, value in trigger.items():
            if key == "active_panel_is":
                panel = str(value)
                if panel != "any" and panel != section:
                    return False
            elif key == "ai_status_is":
                if str(value) != ai_status:
                    return False
            elif key == "selected_artifact_present":
                if bool(value) != artifact_present:
                    return False
            else:
                # event_type_is and unknown keys: cannot evaluate in tick context
                return False
        return True

    # ── action application ────────────────────────────────────────────────────

    def _apply_heuristic_action(
        self,
        action: dict[str, Any],
        game: dict[str, Any],
        *,
        section: str,
    ) -> None:
        kind = str(action.get("kind") or "follow_with_distance")

        if kind == "follow_with_distance":
            game["tutorial_ai_target_mode"] = "follow_user"
            game["ai_snake_follow_distance"] = int(action.get("distance") or 4)
        elif kind == "lurk_near":
            game["tutorial_ai_target_mode"] = "lurk"
            game["ai_snake_follow_distance"] = int(action.get("distance") or 6)
        elif kind == "fast_target":
            game["tutorial_ai_target_mode"] = "fast_target"
            game["ai_snake_follow_distance"] = 2
        else:
            game["tutorial_ai_target_mode"] = "follow_user"
            game["ai_snake_follow_distance"] = 4

        game["tutorial_ai_heuristic_section"] = section

    # ── AI status helper ──────────────────────────────────────────────────────

    def _snake_ai_status(self, game: dict[str, Any]) -> str:
        """Derive AI status string from game state: 'online', 'offline', 'timeout'."""
        runtime_status = str(game.get("ai_snake_runtime_status") or "idle")
        worker_response = game.get("ai_snake_worker_response")
        if isinstance(worker_response, dict):
            ws = str(worker_response.get("status") or "ok")
            if ws == "degraded":
                error = str(worker_response.get("error") or "")
                return "timeout" if "timeout" in error or "stale" in error else "offline"
            if ws == "ok":
                return "online"
        if runtime_status in {"timeout", "error"}:
            return "timeout"
        if runtime_status == "idle":
            return "offline"
        return "online"

    # ── decision trace recording ──────────────────────────────────────────────

    def _record_snake_decision(
        self,
        *,
        heuristic_id: str,
        action_kind: str,
        context_hash: str,
        fallback_reason: str,
        now: float,
    ) -> None:
        try:
            from agent.services.heuristic_runtime.decision_trace import DecisionTrace
        except ImportError:
            return
        trace = DecisionTrace(
            surface="tui_snake",
            context_hash=context_hash,
            heuristic_id=heuristic_id,
            confidence=0.85 if not fallback_reason else 0.4,
            fallback_reason=fallback_reason or None,
            source="heuristic",
            action_kind=action_kind,
            started_at=now,
        )
        trace.resolve(resolved_at=now)
        traces: list = getattr(self, "_heuristic_traces", [])
        traces.append(trace)
        if len(traces) > 200:
            traces = traces[-200:]
        self._heuristic_traces = traces

    # ── proposal generation (async-safe) ─────────────────────────────────────

    def _maybe_generate_proposal(self, *, now: float) -> None:
        """Trigger proposal generation in a background thread when thresholds met.

        Also polls any previously submitted proposal future and surfaces the
        result in the status bar once done.
        """
        # Poll previous proposal future
        proposal_future: Future | None = getattr(self, "_heuristic_proposal_future", None)
        if proposal_future is not None and proposal_future.done():
            self._heuristic_proposal_future = None
            try:
                result = proposal_future.result()
                if result:
                    pid, dominant_id = result
                    game = dict(self.state.header_logo_game or {})
                    if bool(game.get("active")):
                        self._set_state(self.state.with_updates(
                            status_message=f"heuristic proposal generiert: {dominant_id} [{pid}]"
                        ))
            except Exception:
                pass

        # Don't start a new proposal while one is running
        if getattr(self, "_heuristic_proposal_future", None) is not None:
            return

        traces: list = getattr(self, "_heuristic_traces", [])
        last_at: float = getattr(self, "_last_heuristic_proposal_at", 0.0)
        if len(traces) < _PROPOSAL_MIN_TRACES:
            return
        if (now - last_at) < _PROPOSAL_MIN_INTERVAL:
            return

        executor: ThreadPoolExecutor = getattr(self, "_heuristic_load_executor", None)  # type: ignore[assignment]
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="tui-heuristic-loader"
            )
            self._heuristic_load_executor = executor

        traces_snapshot = list(traces)
        self._heuristic_traces = []        # reset optimistically
        self._last_heuristic_proposal_at = now   # prevent double-trigger
        self._heuristic_proposal_future = executor.submit(
            self._run_generate_proposal_bg, traces_snapshot
        )

    def _run_generate_proposal_bg(self, traces_snapshot: list) -> tuple[str, str] | None:
        """Background worker: generate and save proposal, returns (pid, dominant_id)."""
        try:
            from agent.services.heuristic_runtime.proposal_service import ProposalService
            svc = ProposalService()
            result = svc.generate_from_traces(
                traces_snapshot, proposed_by="tui-heuristic-mixin", domain="tui_snake"
            )
            svc.save_candidate(result.proposal)
            return result.proposal.proposal_id[:8], result.dominant_heuristic_id
        except Exception:
            return None
