"""SnakeTickMixin — snake game loop and AI prediction methods extracted from InteractiveOperatorTui.

Contains: _tick_header_snake, _tick_ai_snake_prediction, _update_multi_snake_state

Used as a mixin: class InteractiveOperatorTui(SnakeTickMixin, ...):
All methods use self.* — no back-reference needed beyond normal Python MRO.
"""
from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, cast

from client_surfaces.operator_tui.ai_snake_context import (
    artifact_ref_from_game,
    build_context_envelope_ref,
    compact_observation_summary,
    default_ai_context,
    load_codecompass_artifact,
    relevance_refs_for_intent,
    set_ai_context,
    training_profile_envelope,
)
from client_surfaces.operator_tui.ai_snake_follow import (
    apply_worker_follow_update,
    make_follow_state,
    step_follow_state,
)
from client_surfaces.operator_tui.ai_snake_learning import (
    apply_prediction_feedback,
    merge_patterns,
    mine_patterns_from_events,
)
from client_surfaces.operator_tui.ai_snake_policy import apply_policy_to_payload
from client_surfaces.operator_tui.ai_snake_prediction import build_prediction_trace, quick_predict
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    read_active_profile,
    read_events,
    read_patterns,
    save_patterns,
)
from client_surfaces.operator_tui.models import FocusPane


def _run_learning_cycle_bg(min_cases: int) -> None:
    """Pattern mining cycle — always called from background thread, never main loop."""
    from client_surfaces.operator_tui.ai_snake_training_store import (
        merge_patterns,
        mine_patterns_from_events,
        read_events,
        read_patterns,
        save_patterns,
    )
    events = read_events(max_items=5000)
    mined = mine_patterns_from_events(events=events, min_cases=min_cases)
    if mined:
        merged = merge_patterns(existing=read_patterns(), mined=mined)
        save_patterns(merged, backup=False)


class SnakeTickMixin:
    """Mixin providing snake game-loop tick and AI prediction functionality."""

    # ── T01: Header snake tick ────────────────────────────────────────────────

    def _tick_header_snake(self) -> None:
        if not self._header_snake_enabled():
            return
        # Auto-initialize game state on first tick when it hasn't been set yet
        if not self.state.header_logo_game:
            self.state = self.state.with_updates(header_logo_game=self._default_header_snake())
        game = dict(self.state.header_logo_game)  # type: ignore[arg-type]
        self._maybe_tick_llm_health(game, now=time.monotonic())
        free_mode = bool(game.get("free_mode"))
        # Allow tick when: in HEADER focus, OR ui_steering on, OR tutorial AI is running, OR free_mode active
        if self.state.focus is not FocusPane.HEADER and not game.get("ui_steering") and not game.get("tutorial_mode") and not free_mode:
            self._poll_tutor_ask_result(game)
            self._tick_chat(game, now=time.monotonic())
            self._tick_chat_ai_response(game)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        if not game.get("active", False) or not game.get("alive", True):
            return
        # T01.02: skip tick when paused
        if bool(game.get("paused")):
            self._poll_tutor_ask_result(game)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        # T01.04: speed override via :speed command
        tps_override = game.get("tps_override")
        tps = max(2, min(60, int(tps_override if tps_override else os.environ.get("ANANTA_TUI_HEADER_SNAKE_TPS", "18"))))
        step = 1.0 / tps
        now = time.monotonic()
        last_move = float(game.get("last_move", now))
        if (now - last_move) < step:
            return
        dt = max(step, now - last_move)
        if free_mode:
            size = shutil.get_terminal_size((120, 32))
            board_w = max(24, int(size.columns))
            board_h = max(12, int(size.lines) - 2)  # matches len(rendered shell)
        else:
            board_w = max(18, int(game.get("board_w", 18)))
            board_h = max(6, int(game.get("board_h", 6)))
        game["board_w"] = board_w
        game["board_h"] = board_h
        # T01.03: clamp food to new board boundaries after resize
        food_raw = game.get("food")
        if isinstance(food_raw, (list, tuple)) and len(food_raw) == 2:
            fx, fy = int(food_raw[0]), int(food_raw[1])
            if fx >= board_w or fy >= board_h:
                game["food"] = (fx % board_w, fy % board_h)
        snake_raw = game.get("snake") or []
        snake = [(int(p[0]), int(p[1])) for p in snake_raw if isinstance(p, (list, tuple)) and len(p) == 2]
        if not snake:
            snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        snake = [((x % board_w), (y % board_h)) for x, y in snake]
        trail_raw = game.get("trail_path") or []
        trail_path = [
            (int(p[0]) % board_w, int(p[1]) % board_h)
            for p in trail_raw
            if isinstance(p, (list, tuple)) and len(p) == 2
        ]
        if not trail_path:
            trail_path = list(snake)
        marks_raw = game.get("mark_cells") or []
        marks: list[tuple[int, int, int]] = []
        for item in marks_raw:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                continue
            mx, my, ttl = int(item[0]), int(item[1]), int(item[2])
            if ttl > 0:
                marks.append((mx % board_w, my % board_h, ttl))
        vx = float(game.get("vel_x", 10.0))
        vy = float(game.get("vel_y", 0.0))
        if bool(game.get("mouse_follow_enabled")) and str(game.get("movement_mode") or "") == "mouse_follow":
            mouse = game.get("mouse_state")
            if isinstance(mouse, dict) and bool(mouse.get("active")):
                hx, hy = snake[0]
                mx = max(0, min(board_w - 1, int(mouse.get("x", hx))))
                my = max(0, min(board_h - 1, int(mouse.get("y", hy))))
                dx = mx - hx
                dy = my - hy
                smoothing = max(0.05, min(0.9, float(os.environ.get("ANANTA_TUI_MOUSE_FOLLOW_SMOOTHING", "0.28"))))
                limit = max(6.0, min(100.0, float(os.environ.get("ANANTA_TUI_MOUSE_FOLLOW_MAX_SPEED", "56.0"))))
                vx = (vx * (1.0 - smoothing)) + (dx * smoothing * 8.0)
                vy = (vy * (1.0 - smoothing)) + (dy * smoothing * 8.0)
                vx = max(-limit, min(limit, vx))
                vy = max(-limit, min(limit, vy))
        ax = float(game.get("accum_x", 0.0)) + vx * dt
        ay = float(game.get("accum_y", 0.0)) + vy * dt

        moved = 0
        safety = 120
        while safety > 0 and (abs(ax) >= 1.0 or abs(ay) >= 1.0):
            safety -= 1
            if abs(ax) >= abs(ay):
                sx = 1 if ax > 0 else -1
                sy = 0
                ax -= sx
            else:
                sx = 0
                sy = 1 if ay > 0 else -1
                ay -= sy
            hx, hy = snake[0]
            new_head = ((hx + sx) % board_w, (hy + sy) % board_h)
            snake = [new_head, *snake]
            while len(snake) > 12:
                snake.pop()
            trail_path = [new_head, *trail_path]
            mark_ttl = max(4, min(24, int(os.environ.get("ANANTA_TUI_SNAKE_MARK_TTL", "12"))))
            marks = [(mx, my, ttl - 1) for (mx, my, ttl) in marks if ttl > 1]
            marks.insert(0, (new_head[0], new_head[1], mark_ttl))
            moved += 1

        msg = str(game.get("message") or "")
        trail_max = max(96, min(800, max(len(msg) * 8, 256)))
        trail_path = trail_path[:trail_max]
        marks = marks[:trail_max]
        game["snake"] = snake
        game["trail_path"] = trail_path
        game["mark_cells"] = marks
        if abs(vx) >= abs(vy):
            game["direction"] = (1 if vx > 0 else (-1 if vx < 0 else 0), 0)
        else:
            game["direction"] = (0, 1 if vy > 0 else (-1 if vy < 0 else 0))
        game["next_direction"] = game["direction"]
        game["accum_x"] = ax
        game["accum_y"] = ay
        moves = int(game.get("moves", 0)) + max(1, moved)
        game["moves"] = moves
        game["last_move"] = now
        game["free_mode"] = free_mode

        # T01.05: score = moves // 20, cache highscore
        score = moves // 20
        game["score"] = score
        game["_scores_cache"] = self._scores_cache

        # T02.01: fire milestone events into tutor event queue
        self._fire_score_events(game, score=score)
        # T02.06: idle comment
        self._maybe_fire_idle_comment(game, now=now)
        # T02.03: process pending :ask question
        self._poll_tutor_ask_result(game)
        # T02.04: advance pointer blink frame
        self._tick_tutor_pointer(game, now=now)
        # E04.T04: advance tutorial step if event matches
        self._process_tutorial_event(game, event=self._snake_last_event_fired)
        self._snake_last_event_fired = ""
        # T04.04: guided tour auto-advance
        self._tick_guided_tour(game, now=now)

        # E01: process pending notes ops (pin/unpin/delete/search)
        if game.get("notes_pin_id") or game.get("notes_unpin_id") or game.get("notes_delete_id") or game.get("notes_search_query"):
            self._process_notes_ops(game)

        # E03: poll chat transport and handle incoming messages
        self._tick_chat(game, now=now)

        # E04: sync AI ask result back to AI chat channel
        self._tick_chat_ai_response(game)

        # T01.05: record section visit for first-visit explanation
        current_section = str(self.state.section_id or "dashboard")
        if current_section != getattr(self, "_last_tracked_section", ""):
            self._last_tracked_section = current_section
            self._section_first_visit_pending = current_section

        if self._section_first_visit_pending:
            self._maybe_fire_section_visit_explanation(game, section_id=self._section_first_visit_pending)
            self._section_first_visit_pending = ""

        self._tick_ai_snake_prediction(game, now=now)
        # Heuristic selection: evaluate tui_snake heuristics against current screen state
        self._tick_snake_heuristic(game, now=now)

        # sync tutor depth mode into game state
        game["tutor_depth_mode"] = self._tutor_depth_mode

        self._update_multi_snake_state(game, now=now, board_w=board_w, board_h=board_h)
        mode_label = "fullscreen" if free_mode else "framed"
        speed_level = int(game.get("speed_level") or 3)
        next_state = self.state.with_updates(
            header_logo_game=game,
            status_message=f"snake:{mode_label} speed:{speed_level}/5 vx={vx:.1f} vy={vy:.1f}",
        )
        self.state = self._apply_snake_hover_selection_delay(next_state, head=snake[0], now=now)

    # ── T04: AI snake prediction and worker dispatch ──────────────────────────

    def _tick_ai_snake_prediction(self, game: dict[str, object], *, now: float) -> None:
        section = str(self.state.section_id or "dashboard")
        if (now - float(self._ai_learning_settings_loaded_at or 0.0)) >= 10.0:
            profile_payload = read_active_profile()
            self._ai_learning_settings = (
                dict(profile_payload.get("learning_settings") or {}) if isinstance(profile_payload, dict) else {}
            )
            self._ai_learning_settings_loaded_at = now
        profile_learning = self._ai_learning_settings
        self._ai_training_recorder.set_enabled(bool(profile_learning.get("enabled", True)))
        self._ai_training_recorder.set_paused(bool(profile_learning.get("paused", False)))
        learning_enabled = bool(profile_learning.get("enabled", True))
        learning_paused = bool(profile_learning.get("paused", False)) or bool(game.get("ai_learning_session_paused"))

        # Throttle: section_visit only when section actually changes
        _last_rec_section: str = getattr(self, "_recorder_last_section", "")
        if section != _last_rec_section:
            self._recorder_last_section = section
            self._ai_training_recorder.record_event(
                event_type="section_visit",
                value_norm=section,
                refs=[f"section:{section}"],
                privacy_class="public_ui",
            )
        self._ai_observation.add_event(kind="section", value=section, timestamp=now)
        if bool(game.get("tutorial_mode")):
            self._ai_observation.add_event(kind="chat_channel", value="ai:tutor", timestamp=now)
        artifact_ref = artifact_ref_from_game(game)
        if isinstance(artifact_ref, dict):
            ref_value = str(artifact_ref.get("path") or artifact_ref.get("label") or "artifact")
            ref_id = str(artifact_ref.get("path") or "")
            # Throttle: artifact_focus only when the referenced artifact changes
            _last_rec_artifact: str = getattr(self, "_recorder_last_artifact", "")
            if ref_id != _last_rec_artifact:
                self._recorder_last_artifact = ref_id
                self._ai_training_recorder.record_event(
                    event_type="artifact_focus",
                    value_norm=ref_value,
                    refs=[ref_id] if ref_id else [],
                    privacy_class="workspace",
                )
            self._ai_observation.add_event(
                kind="artifact",
                value=ref_value,
                ref_id=ref_id,
                timestamp=now,
            )
        vx = float(game.get("vel_x") or 0.0)
        vy = float(game.get("vel_y") or 0.0)
        if abs(vx) >= abs(vy):
            movement = "right" if vx > 0.25 else ("left" if vx < -0.25 else "idle")
        else:
            movement = "down" if vy > 0.25 else ("up" if vy < -0.25 else "idle")
        self._ai_observation.add_event(kind="movement", value=movement, timestamp=now)
        # Throttle: movement_vector at most every 400 ms (not 18-24×/s)
        _last_rec_move: float = getattr(self, "_recorder_last_move_at", 0.0)
        if (now - _last_rec_move) >= 0.4:
            self._recorder_last_move_at = now
            self._ai_training_recorder.record_event(
                event_type="movement_vector",
                value_norm=movement,
                refs=[f"section:{section}"],
                privacy_class="public_ui",
            )
        self._ai_observation.add_event(
            kind="notes_active",
            value=bool((game.get("chat_state") or {}).get("notes_context_released")) if isinstance(game.get("chat_state"), dict) else False,
            timestamp=now,
        )
        summary = compact_observation_summary(self._ai_observation.compact_summary(max_facts=20), max_facts=20)
        quick = quick_predict(self._ai_observation.events(), now=now)
        prediction = quick.as_dict()
        # Codecompass artifact: fully async — disk read in background thread, stale cache on main loop
        _cc_loaded_at, _cc_cached = getattr(self, "_codecompass_artifact_cache", (0.0, None))
        _cc_future: Future | None = getattr(self, "_codecompass_artifact_future", None)
        if _cc_future is not None and _cc_future.done():
            try:
                _cc_cached = _cc_future.result()
                _cc_loaded_at = now
                self._codecompass_artifact_cache = (now, _cc_cached)
            except Exception:
                pass
            self._codecompass_artifact_future = None
            _cc_future = None
        if _cc_future is None and (now - _cc_loaded_at) >= 10.0:
            _bg = self._get_snake_bg_executor()
            self._codecompass_artifact_future = _bg.submit(load_codecompass_artifact)
        codecompass = _cc_cached
        ai_ctx = default_ai_context()
        set_ai_context(game, ai_ctx)
        envelope = build_context_envelope_ref(ai_ctx, codecompass_artifact=codecompass, selected_artifact_ref=artifact_ref)
        envelope["retrieval_refs"] = relevance_refs_for_intent(
            intent=str(prediction.get("predicted_intent") or "unknown"),
            codecompass_artifact=codecompass,
            max_refs=12,
        )
        training_ctx = training_profile_envelope(
            intent=str(prediction.get("predicted_intent") or "unknown"),
            max_patterns=int(game.get("ai_snake_training_max_patterns") or 8),
        )
        envelope["training_profile_ref"] = training_ctx.get("training_profile_ref")
        envelope["active_pattern_refs"] = training_ctx.get("active_pattern_refs")
        signature = f"{prediction.get('predicted_intent')}|{prediction.get('target_ref')}|{section}"
        if signature != self._ai_last_signature:
            self._ai_worker_client.cancel_pending_predict(reason="local_signature_changed")
            self._ai_last_signature = signature
        cache_key = self._ai_prediction_cache.make_key(
            section=section,
            target_ref=str(prediction.get("target_ref") or ""),
            intent_kind=str(prediction.get("predicted_intent") or "unknown"),
            context_hash=str(envelope.get("context_hash") or "missing"),
        )
        cached = self._ai_prediction_cache.get(cache_key, now=now)
        cache_hit = cached is not None
        if not cache_hit:
            self._ai_prediction_cache.set(cache_key, prediction, now=now)
        gate_decision = self._ai_prediction_gate.evaluate(
            prediction=quick,
            signature=signature,
            now=now,
            selected_artifact=isinstance(artifact_ref, dict),
        )
        prediction_trace = build_prediction_trace(
            mode="predict_intent",
            prediction=quick,
            context_hash=str(envelope.get("context_hash") or "missing"),
            used_refs=list(envelope.get("retrieval_refs") or []),
            provider_ref="local_quick",
            cache_hit=cache_hit,
            skipped_reason=gate_decision.reason if not gate_decision.allow_worker_request else "",
        )
        selected_allowed = isinstance(artifact_ref, dict) or str(prediction.get("target_ref") or "").startswith("section:")
        notes_released = bool((game.get("chat_state") or {}).get("notes_context_released")) if isinstance(game.get("chat_state"), dict) else False
        worker_payload, worker_policy = apply_policy_to_payload(
            {
                "mode": str(game.get("ai_snake_mode") or "lurking_follow"),
                "quick_prediction": prediction,
                "context_envelope_ref": envelope,
                "observation_summary": summary,
                "notes_context": (game.get("chat_state") or {}).get("notes_context"),
            },
            boundary="worker_request",
            notes_released=notes_released,
            selected_artifact_allowed=selected_allowed,
            external_provider=False,
            training_context_allowed=bool(game.get("ai_training_context_released")),
        )
        prompt_payload, prompt_policy = apply_policy_to_payload(
            {
                "quick_prediction": prediction,
                "observation_summary": summary,
                "notes_context": (game.get("chat_state") or {}).get("notes_context"),
            },
            boundary="lmstudio_prompt",
            notes_released=notes_released,
            selected_artifact_allowed=selected_allowed,
            external_provider=False,
            training_context_allowed=bool(game.get("ai_training_context_released")),
        )
        if (
            gate_decision.allow_worker_request
            and not cache_hit
            and worker_policy.allowed
            and str(game.get("ai_snake_mode") or "lurking_follow") != "off"
            and isinstance(worker_payload, dict)
            and not bool(worker_payload.get("blocked"))
        ):
            game["ai_snake_budget_deny_reason"] = ""
            prompt = self._ai_worker_client.render_prompt(
                mode="predict_intent",
                observation_summary=dict(worker_payload.get("observation_summary") or {}),
                context_envelope_ref=dict(worker_payload.get("context_envelope_ref") or {}),
                max_chars=int(self._ai_lm_budget.max_prompt_chars),
            )
            budget_allowed, budget_reason = self._ai_lm_budget.allow_predict(prompt=prompt, now=now)
            request = self._ai_worker_client.build_request(
                mode="predict_intent",
                observation_summary=dict(worker_payload.get("observation_summary") or {}),
                quick_prediction=dict(worker_payload.get("quick_prediction") or {}),
                context_envelope_ref=dict(worker_payload.get("context_envelope_ref") or {}),
                provider_selection={
                    "provider_preference": str(game.get("ai_snake_provider_preference") or "lmstudio"),
                    "model": str(game.get("ai_snake_provider_model") or "ananta-smoke"),
                    "cloud_allowed": bool(game.get("ai_snake_provider_cloud_allowed")),
                },
                max_latency_ms=max(250, int(game.get("ai_snake_provider_max_latency_ms") or 2000)),
            )
            if budget_allowed:
                submitted = self._ai_worker_client.submit(request, signature=signature)
                if submitted is not None:
                    self._ai_worker_task = submitted
            else:
                game["ai_snake_budget_deny_reason"] = budget_reason

        worker_result: dict[str, Any] | None = None
        if self._ai_worker_task is not None:
            worker_result = self._ai_worker_client.poll(self._ai_worker_task, now=now, current_signature=signature)
            if worker_result is not None:
                self._ai_worker_task = None
                game["ai_snake_worker_response"] = worker_result
                if worker_result.get("status") == "ok":
                    prediction_trace["provider_ref"] = str(worker_result.get("provenance_ref") or "worker:default")
                if (
                    worker_result.get("status") == "degraded"
                    and str(worker_result.get("error") or "") == "timeout"
                    and isinstance(game.get("chat_state"), dict)
                ):
                    from client_surfaces.operator_tui.chat_state import (
                        ChannelType,
                        DeliveryState,
                        SenderKind,
                        append_message,
                        make_message,
                    )
                    msg = make_message(
                        channel_id="ai:tutor",
                        channel_type=ChannelType.AI,
                        sender_id="system",
                        sender_kind=SenderKind.SYSTEM,
                        text="* [system] AI worker timeout – nutze lokale Prediction.",
                        delivery_state=DeliveryState.RECEIVED,
                    )
                    append_message(cast(dict[str, Any], game["chat_state"]), msg)

        ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
        follow_state_raw = game.get("ai_snake_follow_state")
        follow_state = dict(follow_state_raw) if isinstance(follow_state_raw, dict) else make_follow_state(mode=ai_mode)
        local_snake = game.get("snake")
        if isinstance(local_snake, list) and local_snake:
            head = local_snake[0]
            if isinstance(head, (list, tuple)) and len(head) == 2:
                follow_state["mode"] = ai_mode
                follow_state = step_follow_state(
                    follow_state,
                    user_position=(int(head[0]), int(head[1])),
                    board_w=max(1, int(game.get("board_w") or 18)),
                    board_h=max(1, int(game.get("board_h") or 6)),
                )
        response = game.get("ai_snake_worker_response")
        if isinstance(response, dict) and str(response.get("status") or "ok") == "ok":
            if float(response.get("expires_at") or 0.0) < now:
                response = {"status": "degraded", "error": "stale_result"}
                game["ai_snake_worker_response"] = response
            elif float(response.get("confidence") or 0.0) >= 0.65:
                prediction = {
                    **prediction,
                    "predicted_intent": str(response.get("predicted_intent") or prediction.get("predicted_intent") or "unknown"),
                    "target_ref": str(response.get("target_ref") or prediction.get("target_ref") or ""),
                    "confidence": float(response.get("confidence") or prediction.get("confidence") or 0.0),
                    "expires_at": float(response.get("expires_at") or prediction.get("expires_at") or now + 20.0),
                }
                follow_state = apply_worker_follow_update(
                    follow_state,
                    follow_mode_update=str(response.get("follow_mode_update") or ""),
                    prediction_target=str(response.get("target_ref") or ""),
                    confidence=float(response.get("confidence") or 0.0),
                )

        runtime_status = "idle"
        worker_response = game.get("ai_snake_worker_response") if isinstance(game.get("ai_snake_worker_response"), dict) else {}
        if ai_mode == "off":
            runtime_status = "off"
        elif str(worker_response.get("status") or "") == "degraded":
            runtime_status = "degraded"
        elif self._ai_worker_task is not None:
            runtime_status = "thinking"
        elif cache_hit:
            runtime_status = "context-ready"
        elif gate_decision.reason in {"prediction_not_stable", "rate_limited"}:
            runtime_status = "predicting"
        elif ai_mode == "quiet":
            runtime_status = "quiet"
        elif str(follow_state.get("mode") or "") == "follow":
            runtime_status = "following"
        elif str(follow_state.get("mode") or "") == "lurking":
            runtime_status = "lurking"
        allow_proactive_comment = (
            gate_decision.allow_worker_request
            and float(prediction.get("confidence") or 0.0) >= 0.65
            and ai_mode != "off"
            and prompt_policy.allowed
        )
        active_pattern_refs = list(envelope.get("active_pattern_refs") or [])
        matched_pattern_id = ""
        for item in active_pattern_refs:
            if not isinstance(item, dict):
                continue
            if str(item.get("predicted_intent") or "").strip().lower() == str(prediction.get("predicted_intent") or "").strip().lower():
                matched_pattern_id = str(item.get("pattern_id") or "")
                break
        if not matched_pattern_id and active_pattern_refs and isinstance(active_pattern_refs[0], dict):
            matched_pattern_id = str(active_pattern_refs[0].get("pattern_id") or "")
        prediction_source = "local_quick"
        if matched_pattern_id:
            prediction_source = "learned_profile"
        if isinstance(worker_response, dict) and str(worker_response.get("status") or "") == "ok":
            prediction_source = "worker_context"
        if isinstance(game.get("chat_state"), dict):
            forced = bool(game.pop("ai_force_question", False))
            if allow_proactive_comment or forced:
                self._route_prediction_comment_to_monitor(
                    game,
                    prediction=prediction,
                    now=now,
                    quiet=(ai_mode == "quiet"),
                    forced=forced,
                    cooldown_seconds=20,
                )

        target_ref = str(prediction.get("target_ref") or "")
        reached_target = False
        if target_ref.startswith("section:"):
            reached_target = target_ref.removeprefix("section:") == section
        elif target_ref and isinstance(artifact_ref, dict):
            artifact_path = str(artifact_ref.get("path") or artifact_ref.get("label") or "")
            reached_target = bool(artifact_path) and artifact_path in target_ref
        auto_feedback_key = f"{target_ref}|{section}"
        if (
            learning_enabled
            and not learning_paused
            and reached_target
            and target_ref
            and str(game.get("ai_last_auto_feedback_key") or "") != auto_feedback_key
        ):
            patterns = read_patterns()
            updated, changed = apply_prediction_feedback(patterns=patterns, target_ref=target_ref, positive=True)
            if changed:
                save_patterns(updated, backup=False)
            append_behavior_event(
                event_type="prediction_feedback",
                value_norm="implicit_good",
                refs=[target_ref],
                privacy_class="workspace",
                retention_hint="rolling_30d",
                reason="target_reached",
            )
            game["ai_last_auto_feedback_key"] = auto_feedback_key

        min_cases = max(1, int(profile_learning.get("evidence_min_cases") or 3))
        # Poll completed mining future
        _mining_future: Future | None = getattr(self, "_ai_mining_future", None)
        if _mining_future is not None and _mining_future.done():
            self._ai_mining_future = None
        # Mining loop: runs in background executor, never on main thread
        if (
            _mining_future is None
            and learning_enabled
            and not learning_paused
            and (now - float(self._ai_learning_last_mined_at or 0.0)) >= 30.0
        ):
            self._ai_learning_last_mined_at = now   # prevent double-submit
            _bg = self._get_snake_bg_executor()
            self._ai_mining_future = _bg.submit(
                _run_learning_cycle_bg, min_cases
            )

        # Flush recorder queue to disk in background at most once per second
        _flush_at: float = getattr(self, "_recorder_flush_at", 0.0)
        if (now - _flush_at) >= 1.0:
            self._recorder_flush_at = now
            self._get_snake_bg_executor().submit(self._ai_training_recorder.flush_queued)

        game["ai_snake_prediction"] = prediction
        game["ai_snake_context_envelope"] = envelope
        game["ai_snake_follow_state"] = follow_state
        game["ai_snake_runtime_status"] = runtime_status
        game["ai_snake_debug"] = {
            "observation_summary": summary,
            "cache_hit": cache_hit,
            "gate_reason": gate_decision.reason,
            "skipped_worker_requests": gate_decision.skipped_worker_requests,
            "allow_worker_request": gate_decision.allow_worker_request,
            "policy": {
                "worker_request": worker_policy.as_dict(),
                "lmstudio_prompt": prompt_policy.as_dict(),
            },
            "policy_payload_preview": {
                "worker_request": worker_payload,
                "lmstudio_prompt": prompt_payload,
            },
            "allow_proactive_comment": allow_proactive_comment,
            "lm_budget": self._ai_lm_budget.debug_state(now=now),
            "budget_deny_reason": str(game.get("ai_snake_budget_deny_reason") or ""),
            "worker_result_status": str((game.get("ai_snake_worker_response") or {}).get("status") or ""),
            "worker_result_error": str((game.get("ai_snake_worker_response") or {}).get("error") or ""),
            "pending_worker_request": self._ai_worker_task is not None,
            "last_prediction_trace": prediction_trace,
            "training_profile_ref": envelope.get("training_profile_ref"),
            "active_pattern_refs": active_pattern_refs,
            "matched_pattern_id": matched_pattern_id,
            "prediction_source": prediction_source,
        }

    def _route_prediction_comment_to_monitor(
        self,
        game: dict[str, object],
        *,
        prediction: dict[str, object],
        now: float,
        quiet: bool,
        forced: bool = False,
        cooldown_seconds: int = 20,
    ) -> bool:
        if quiet and not forced:
            return False
        confidence = float(prediction.get("confidence") or 0.0)
        if confidence < 0.65 and not forced:
            return False
        chat_raw = game.get("chat_state")
        if not isinstance(chat_raw, dict):
            return False
        chat = cast(dict[str, Any], chat_raw)
        last_comment_at = float(chat.get("ai_last_proactive_comment_at") or 0.0)
        if (now - last_comment_at) < max(5, int(cooldown_seconds)) and not forced:
            return False
        intent = str(prediction.get("predicted_intent") or "unknown")
        target_ref = str(prediction.get("target_ref") or "").strip() or "aktuellen Bereich"
        text = f"Ich glaube, du willst zu {target_ref} ({intent}, conf={confidence:.2f})."
        if hasattr(self, "_append_ai_monitor_log"):
            try:
                self._append_ai_monitor_log(game, event="prediction_comment", label=text)  # type: ignore[attr-defined]
            except Exception:
                return False
        else:
            return False
        chat["ai_last_proactive_comment_at"] = float(now)
        game["chat_state"] = chat
        return True

    # ── Background executor (shared across all snake I/O) ─────────────────────

    def _get_snake_bg_executor(self) -> ThreadPoolExecutor:
        executor: ThreadPoolExecutor | None = getattr(self, "_snake_bg_executor", None)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tui-snake-io")
            self._snake_bg_executor = executor
        return executor

    # ── Multi-snake state ─────────────────────────────────────────────────────

    def _update_multi_snake_state(
        self,
        game: dict[str, object],
        *,
        now: float,
        board_w: int,
        board_h: int,
    ) -> None:
        snakes_raw = game.get("snakes")
        snakes: dict[str, dict[str, object]]
        if isinstance(snakes_raw, dict):
            snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)}
        else:
            snakes = {}
        local_id = str(game.get("local_snake_id") or "s1")
        local_pseudonym = str(game.get("pseudonym") or os.environ.get("ANANTA_TUI_SNAKE_PSEUDONYM", "local-snake"))
        local_provider = str(game.get("oidc_provider") or os.environ.get("ANANTA_TUI_SNAKE_OIDC_PROVIDER", "local"))
        local_snapshot = {
            "id": local_id,
            "pseudonym": local_pseudonym,
            "oidc_provider": local_provider,
            "snake": list(game.get("snake") or []),
            "trail_path": list(game.get("trail_path") or []),
            "mark_cells": list(game.get("mark_cells") or []),
            "selection_cells": list(game.get("selection_cells") or []),
            "selection_regions": list(game.get("selection_regions") or []),
            "message": str(game.get("message") or ""),
            "message_style": str(game.get("message_style") or "trail"),
            "snake_color": str(game.get("snake_color") or "mint"),
            "trail_window": int(game.get("trail_window") or 10),
            "trail_speed": float(game.get("trail_speed") or 8.0),
            "active": True,
            "updated_at": now,
            "local": True,
            "access_level": "full",
        }
        snakes[local_id] = local_snapshot
        self._update_demo_remote_snakes(snakes, now=now, board_w=board_w, board_h=board_h)
        self._update_tutorial_ai_snake(game, snakes, now=now, board_w=board_w, board_h=board_h, enabled=bool(game.get("tutorial_mode")))
        game["snakes"] = snakes
        game["local_snake_id"] = local_id
