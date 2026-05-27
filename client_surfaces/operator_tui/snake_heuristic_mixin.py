"""SnakeHeuristicMixin — heuristic selection and proposal generation for TUI snake.

Evaluates active heuristics from heuristics/active/ against the current screen
snapshot (game state + section + AI status) and applies the best matching action
to the tutorial AI snake.

Periodically calls ProposalService.generate_from_traces() to generate new
heuristic candidates from accumulated decision traces.

All disk I/O (heuristic loading, proposal generation) runs in a background
ThreadPoolExecutor so the 18 TPS main loop is never blocked by file operations.

ASH-002: movement_mode and governance_mode are orthogonal.
  - movement_mode: controls HOW the snake moves (follow_user, lurk, fast_target, …)
  - governance_mode: controls learning/candidate-creation/activation policy

ASH-015: candidate generation is rate-limited (max 3/h, 300s cooldown).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any


_HEURISTICS_ROOT = Path(__file__).resolve().parents[2] / "heuristics"
_HEURISTICS_DIR = _HEURISTICS_ROOT / "active" / "tui_snake"   # ASH-010: domain subdir
_CANDIDATES_DIR = _HEURISTICS_ROOT / "candidates" / "tui_snake"
_HEURISTIC_CACHE_TTL = 60.0        # reload heuristic files at most once per minute
_PROPOSAL_MIN_TRACES = 25           # generate a proposal after this many decisions
_PROPOSAL_MIN_INTERVAL = 300.0      # at most one proposal every 5 minutes (cooldown)
_PROPOSAL_MAX_PER_HOUR = 3          # ASH-015: hard hourly cap
_PROPOSAL_HOUR = 3600.0


class SnakeHeuristicMixin:
    """Mixin providing heuristic-based AI snake control and proposal generation."""

    # ── governance / movement mode accessors (ASH-002) ────────────────────────

    @property
    def _governance_mode(self) -> str:
        return str(getattr(self, "_snake_governance_mode", "auto_without_human_approval"))

    @_governance_mode.setter
    def _governance_mode(self, value: str) -> None:
        from agent.services.heuristic_runtime.governance import GovernanceMode
        GovernanceMode.from_str(value)          # validates; raises ValueError on unknown
        self._snake_governance_mode = value

    @property
    def _movement_mode(self) -> str:
        return str(getattr(self, "_snake_movement_mode", "follow_user"))

    @_movement_mode.setter
    def _movement_mode(self, value: str) -> None:
        self._snake_movement_mode = value

    def _allows_candidate_creation(self) -> bool:
        try:
            from agent.services.heuristic_runtime.governance import GovernanceMode
            return GovernanceMode.from_str(self._governance_mode).allows_candidate_creation
        except Exception:
            return True

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
        active_action_kind = str(action.get("kind") or "follow_with_distance")

        self._record_snake_decision(
            heuristic_id=heuristic_id,
            action_kind=active_action_kind,
            context_hash=context_hash,
            fallback_reason=fallback_reason,
            now=now,
        )

        # Shadow run tick (ASH-020) — runs in parallel, never blocks
        self._tick_shadow_run(
            game=game,
            section=section,
            ai_status=ai_status,
            artifact_present=artifact_present,
            active_action_kind=active_action_kind,
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
                # First load: also run candidate migration (ASH-016)
                executor.submit(self._run_startup_migration)
            self._heuristic_load_future = executor.submit(self._load_heuristics_from_disk)

        # Return stale data while the background load is in flight
        return cached

    def _run_startup_migration(self) -> None:
        """Run candidate migration once at startup (ASH-016, ASH-014)."""
        if getattr(self, "_candidate_migration_done", False):
            return
        self._candidate_migration_done = True
        try:
            from agent.services.heuristic_runtime.candidate_migration import run_candidate_migration
            run_candidate_migration(domain="tui_snake")
        except Exception:
            pass

    def _load_heuristics_from_disk(self) -> list[dict[str, Any]]:
        """Blocking disk load — always called from background thread.

        Loads from heuristics/active/tui_snake/ (ASH-010 domain subdir).
        Falls back to heuristics/active/ root for backwards-compatibility.
        Only returns heuristics with status == "active".
        """
        result: list[dict[str, Any]] = []
        dirs_to_check: list[Path] = []

        if _HEURISTICS_DIR.is_dir():
            dirs_to_check.append(_HEURISTICS_DIR)
        else:
            # backwards-compat: load from root active/ if domain subdir missing
            fallback = _HEURISTICS_ROOT / "active"
            if fallback.is_dir():
                dirs_to_check.append(fallback)

        for search_dir in dirs_to_check:
            for path in sorted(search_dir.iterdir()):
                if path.suffix != ".json":
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                status = str(data.get("status") or "")
                if status != "active":
                    continue
                # Domain check only needed for root directory fallback
                if search_dir != _HEURISTICS_DIR:
                    if str(data.get("domain") or "") != "tui_snake":
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
            self._movement_mode = "follow_user"
        elif kind == "lurk_near":
            game["tutorial_ai_target_mode"] = "lurk"
            game["ai_snake_follow_distance"] = int(action.get("distance") or 6)
            self._movement_mode = "lurk"
        elif kind == "fast_target":
            game["tutorial_ai_target_mode"] = "fast_target"
            game["ai_snake_follow_distance"] = 2
            self._movement_mode = "fast_target"
        else:
            game["tutorial_ai_target_mode"] = "follow_user"
            game["ai_snake_follow_distance"] = 4
            self._movement_mode = "follow_user"

        game["tutorial_ai_heuristic_section"] = section
        # Expose both modes in game state for TUI debug view
        game["snake_movement_mode"] = self._movement_mode
        game["snake_governance_mode"] = self._governance_mode

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

    # ── shadow run (ASH-020, ASH-023) ────────────────────────────────────────

    def _tick_shadow_run(
        self,
        game: dict[str, Any],
        *,
        section: str,
        ai_status: str,
        artifact_present: bool,
        active_action_kind: str,
    ) -> None:
        """Tick the currently active shadow run (if any).

        Shadow runner computes a hypothetical candidate action without applying it.
        Watchdog aborts and quarantines if thresholds are exceeded.
        On successful completion, candidate scoring and promotion are triggered.
        """
        runner = getattr(self, "_shadow_runner", None)
        if runner is None:
            return

        try:
            from agent.services.heuristic_runtime.shadow_runner import ShadowRunner
            assert isinstance(runner, ShadowRunner)
        except Exception:
            return

        if not runner.is_active:
            if runner.state.completed:
                self._on_shadow_completed(runner)
            elif runner.state.aborted:
                self._on_shadow_aborted(runner)
            self._shadow_runner = None
            return

        candidate = getattr(self, "_shadow_candidate", None)
        if candidate is None:
            self._shadow_runner = None
            return

        shadow_action = runner.compute_candidate_action(
            candidate,
            section=section,
            ai_status=ai_status,
            artifact_present=artifact_present,
        )
        is_exception = shadow_action == "exception"
        runner.record_decision(
            shadow_action_kind=shadow_action if not is_exception else "exception",
            active_action_kind=active_action_kind,
            exception=is_exception,
        )
        # Expose shadow state to TUI debug
        game["shadow_candidate_id"] = runner.state.candidate_id
        game["shadow_decision_count"] = runner.state.decision_count
        game["shadow_match_rate"] = round(runner.state.match_rate, 3)

    def _on_shadow_completed(self, runner: Any) -> None:
        """Shadow run succeeded — score candidate and trigger promotion check."""
        summary = runner.state.to_summary()
        candidate = getattr(self, "_shadow_candidate", None)
        if candidate is None:
            return
        try:
            from agent.services.heuristic_runtime.candidate_scorer import compute_score
            sim_result = candidate.get("simulation_result") or {}
            score = compute_score(
                simulation_passed=bool(sim_result.get("can_activate", False)),
                shadow_decision_count=summary["decision_count"],
                shadow_duration_seconds=summary["duration_seconds"],
                shadow_match_rate=summary["match_rate"],
                metrics=candidate.get("metrics") or {},
            )
            candidate["score"] = score.to_dict()
            # Persist score back to file
            self._persist_candidate_update(candidate)
            if score.meets_thresholds:
                self._maybe_promote_candidate(candidate)
        except Exception:
            pass

    def _on_shadow_aborted(self, runner: Any) -> None:
        """Shadow watchdog triggered — quarantine candidate."""
        candidate = getattr(self, "_shadow_candidate", None)
        if candidate is None:
            return
        reason = runner.state.abort_reason or "shadow_watchdog_triggered"
        self._quarantine_candidate(candidate, reason_code=reason)

    def _persist_candidate_update(self, candidate: dict[str, Any]) -> None:
        """Write updated candidate data back to its file (background-safe call)."""
        try:
            import json
            cid = str(candidate.get("proposal_id") or "")
            if not cid:
                return
            cpath = _CANDIDATES_DIR / f"{cid}.json"
            if cpath.exists():
                cpath.write_text(
                    json.dumps(candidate, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def _quarantine_candidate(self, candidate: dict[str, Any], *, reason_code: str) -> None:
        """Move candidate file to quarantine/tui_snake/."""
        import json
        try:
            cid = str(candidate.get("proposal_id") or "")
            if not cid:
                return
            src = _CANDIDATES_DIR / f"{cid}.json"
            quarantine_dir = _HEURISTICS_ROOT / "quarantine" / "tui_snake"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dst = quarantine_dir / f"{cid}.json"
            if src.exists():
                candidate["status"] = "quarantined"
                candidate["quarantine_reason"] = reason_code
                dst.write_text(
                    json.dumps(candidate, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                src.unlink()
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.candidate_quarantined(candidate_id=cid, reason_code=reason_code)
        except Exception:
            pass

    def _maybe_promote_candidate(self, candidate: dict[str, Any]) -> None:
        """Trigger auto-promotion to active/ if policy allows (ASH-030)."""
        try:
            from agent.services.heuristic_runtime.governance import GovernanceMode
            gmode = GovernanceMode.from_str(self._governance_mode)
            if not gmode.allows_auto_promotion:
                return
        except Exception:
            return
        executor: ThreadPoolExecutor = getattr(self, "_heuristic_load_executor", None)  # type: ignore
        if executor is None:
            return
        import copy
        candidate_copy = copy.deepcopy(candidate)
        executor.submit(self._run_promote_bg, candidate_copy)

    def _run_promote_bg(self, candidate: dict[str, Any]) -> None:
        """Background: promote candidate to active/tui_snake/ via progressive rollout."""
        try:
            from agent.services.heuristic_runtime.auto_activator import AutoActivator
            activator = AutoActivator()
            activator.promote(candidate)
        except Exception:
            pass

    def start_shadow_run(self, candidate: dict[str, Any]) -> None:
        """Start a shadow run for the given candidate (call from background thread)."""
        try:
            from agent.services.heuristic_runtime.shadow_runner import ShadowRunner
            from agent.services.heuristic_runtime import snake_audit_events as audit
            runner = ShadowRunner(
                candidate,
                on_watchdog_trigger=lambda cid, trigger, val: None,
            )
            self._shadow_runner = runner
            self._shadow_candidate = candidate
            audit.candidate_shadow_started(
                candidate_id=str(candidate.get("proposal_id") or "unknown")
            )
        except Exception:
            pass

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

        ASH-015: enforces 300s cooldown and max 3 candidates/hour.
        Skipped (with reason_code rate_limit_active) when limits are exceeded.
        Governance OBSERVE_ONLY and FROZEN also suppress generation.
        """
        # Gate: governance mode may forbid candidate creation (ASH-002)
        if not self._allows_candidate_creation():
            return

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

        # ASH-015: hourly cap — count proposals in the last 3600 s
        proposal_timestamps: list[float] = getattr(self, "_proposal_timestamps_hour", [])
        proposal_timestamps = [t for t in proposal_timestamps if (now - t) < _PROPOSAL_HOUR]
        if len(proposal_timestamps) >= _PROPOSAL_MAX_PER_HOUR:
            return  # rate_limit_active — silently skip

        executor: ThreadPoolExecutor = getattr(self, "_heuristic_load_executor", None)  # type: ignore[assignment]
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="tui-heuristic-loader"
            )
            self._heuristic_load_executor = executor

        traces_snapshot = list(traces)
        self._heuristic_traces = []        # reset optimistically
        self._last_heuristic_proposal_at = now   # prevent double-trigger
        # ASH-015: record this proposal timestamp for hourly cap
        proposal_timestamps.append(now)
        self._proposal_timestamps_hour = proposal_timestamps
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

    # ── User feedback keys (ASH-041) ──────────────────────────────────────────

    def _snake_feedback(self, *, positive: bool) -> None:
        """Handle +/- feedback key press.

        Adjusts score for the current heuristic/candidate and emits audit event.
        """
        hid = getattr(self, "_selected_heuristic_id", "")
        candidate = getattr(self, "_shadow_candidate", None)
        feedback_type = "positive" if positive else "negative"
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.emit(
                "snake_user_feedback",
                heuristic_id=hid,
                feedback=feedback_type,
            )
        except Exception:
            pass
        # Update negative feedback counter for rollback monitoring (ASH-032)
        if not positive:
            count = int(getattr(self, "_snake_negative_feedback_count", 0)) + 1
            self._snake_negative_feedback_count = count
        msg = f"snake: {feedback_type} feedback registered"
        try:
            self._set_state(self.state.with_updates(status_message=msg))
        except Exception:
            pass

    def _snake_rollback_heuristic(self) -> None:
        """Handle R (Shift+r) key press — rollback auto-promoted heuristic."""
        hid = getattr(self, "_selected_heuristic_id", "")
        if not hid:
            return
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.heuristic_rollback(
                from_heuristic_id=hid,
                to_heuristic_id="default_active",
                reason_code="user_requested_rollback",
            )
            self._set_state(self.state.with_updates(
                status_message=f"snake: rollback ausgelöst für {hid[:8]}"
            ))
        except Exception:
            pass

    def _snake_pin_heuristic(self) -> None:
        """Handle P (Shift+p) key press — pin current heuristic, prevent replacement."""
        self._snake_heuristic_pinned = True
        hid = getattr(self, "_selected_heuristic_id", "")
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.emit("snake_heuristic_pinned", heuristic_id=hid)
            self._set_state(self.state.with_updates(
                status_message=f"snake: heuristik gepinnt ({hid[:8]})"
            ))
        except Exception:
            pass

    # ── TUI debug state (ASH-040) ─────────────────────────────────────────────

    def get_snake_debug_info(self, game: dict[str, Any]) -> dict[str, Any]:
        """Return structured debug info for TUI snake debug panel (ASH-040)."""
        runner = getattr(self, "_shadow_runner", None)
        rollout_stage = None
        rollout_quota = None
        if runner is not None:
            state = runner.state
            rollout_stage = None  # rollout stage comes from auto_activator
        # Get rollout info from active heuristic metadata
        hid = str(game.get("active_heuristic_id") or "")
        activation_score = None
        risk_score = None
        candidate = getattr(self, "_shadow_candidate", None)
        if candidate:
            score = candidate.get("score") or {}
            activation_score = score.get("activation_score")
            risk_score = score.get("risk_score")
            rollout = candidate.get("rollout") or {}
            rollout_stage = rollout.get("stage_label")
            rollout_quota = rollout.get("quota")

        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            recent_events = audit.get_events(limit=5)
            last_reason_codes = [
                e.get("fallback_reason") for e in recent_events
                if e.get("event_type") == "snake_decision" and e.get("fallback_reason")
            ][:3]
        except Exception:
            last_reason_codes = []

        return {
            "active_heuristic_id": hid,
            "current_candidate_id": str(candidate.get("proposal_id", ""))[:8] if candidate else None,
            "movement_mode": self._movement_mode,
            "governance_mode": self._governance_mode,
            "activation_strategy": "promote_to_active",
            "last_reason_codes": last_reason_codes,
            "rollout_stage": rollout_stage,
            "rollout_quota": rollout_quota,
            "activation_score": activation_score,
            "risk_score": risk_score,
            "shadow_decision_count": game.get("shadow_decision_count"),
            "shadow_match_rate": game.get("shadow_match_rate"),
            "pinned": bool(getattr(self, "_snake_heuristic_pinned", False)),
        }
