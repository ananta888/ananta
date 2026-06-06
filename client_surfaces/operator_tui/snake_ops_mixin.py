"""SnakeOpsMixin — snake operations, tutor events, and guided tour methods.

Contains: _update_demo_remote_snakes, _apply_snake_hover_selection_delay,
          _snake_mode_active, _toggle_snake_mode, _toggle_tutorial_ai_mode,
          _toggle_snake_pause, _fire_score_events, _queue_tutor_event,
          _dequeue_tutor_event, _get_tutor_text, _get_idle_tutor_text,
          _maybe_fire_idle_comment, _inject_tutor_tip, _maybe_set_tutor_pointer,
          _tick_tutor_pointer, _maybe_fire_section_visit_explanation,
          _tick_guided_tour, _advance_guided_tour_now, _snake_role_for,
          _snake_cycle_message_style, _snake_cycle_color, _snake_head,
          _snake_toggle_selection, _snake_toggle_frame_mode,
          _snake_clear_visual_marks, _snake_commit_frame_selection,
          _snake_render_plain_lines, _snake_copy_selection, _snake_replace_selection,
          _snake_message_mode_active, _toggle_snake_message_mode,
          _snake_message_append, _snake_message_backspace, _snake_cancel_message,
          _snake_commit_message, _save_snake_message_config, _load_snake_message_config,
          _snake_immediate_brake, _snake_escape_target, _apply_snake_escape,
          _apply_snake_section_target, _apply_snake_ui_controls,
          _compute_control_boxes, _box_hit_target, _ensure_snake_escape_gaps,
          _compute_snake_escape_gaps, _spawn_snake_food
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from client_runtime import process
from prompt_toolkit.formatted_text import ANSI

from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.keybindings_config import display_for_action
from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import SECTIONS
from client_surfaces.operator_tui.tui_snapshot import clipboard_safe_text

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class SnakeOpsMixin:
    """Mixin providing snake operations, tutor event handling, and guided tour."""

    def _update_demo_remote_snakes(
        self,
        snakes: dict[str, dict[str, object]],
        *,
        now: float,
        board_w: int,
        board_h: int,
    ) -> None:
        demo_peers = max(0, min(3, int(os.environ.get("ANANTA_TUI_SNAKE_DEMO_PEERS", "0"))))
        if demo_peers <= 0:
            return
        radius_x = max(3, board_w // 7)
        radius_y = max(2, board_h // 5)
        center_x = board_w // 2
        center_y = board_h // 2
        for i in range(demo_peers):
            sid = f"s{i + 2}"
            existing = snakes.get(sid, {})
            access_level = str(existing.get("access_level") or "cancel")
            phase = now * (0.9 + i * 0.3)
            hx = int(center_x + radius_x * math.sin(phase + i * 1.7)) % max(1, board_w)
            hy = int(center_y + radius_y * math.cos(phase + i * 1.3)) % max(1, board_h)
            target_pixel = PixelPoint(float(hx * 8), float(hy * 16))
            prev_px = float(existing.get("pixel_x") or target_pixel.x)
            prev_py = float(existing.get("pixel_y") or target_pixel.y)
            intent_level = str((self.state.header_logo_game or {}).get("artifact_intent_confidence") or "none")
            speed = pixel_boost_speed(base_speed=2.2 + i * 0.4, artifact_intent=intent_level)
            smoothed = smooth_follow(
                current=PixelPoint(prev_px, prev_py),
                target=target_pixel,
                speed=speed,
                dt=max(0.01, min(0.25, 0.08 + (i * 0.02))),
            )
            body = []
            for j in range(8):
                bx = (hx - (j % 4)) % max(1, board_w)
                by = (hy - (j // 4)) % max(1, board_h)
                body.append((bx, by))
            trail = list(body)
            min_x = max(0, hx - 1)
            max_x = min(max(0, board_w - 1), hx + 1)
            min_y = max(0, hy - 1)
            max_y = min(max(0, board_h - 1), hy + 1)
            selection_cells = [(x, y) for y in range(min_y, max_y + 1) for x in (min_x, max_x)]
            selection_cells += [(x, y) for x in range(min_x, max_x + 1) for y in (min_y, max_y)]
            snakes[sid] = {
                "id": sid,
                "pseudonym": f"peer-{i + 2}",
                "oidc_provider": "demo-oidc",
                "snake": body,
                "trail_path": trail,
                "selection_cells": selection_cells,
                "message": f"peer-{i + 2}",
                "message_style": ("orbit" if i % 2 == 0 else "trail"),
                "snake_color": ("cyan" if i % 2 == 0 else "violet"),
                "trail_window": 10,
                "trail_speed": 8.0,
                "active": True,
                "updated_at": now,
                "local": False,
                "access_level": access_level,
                "pixel_x": round(smoothed.x, 3),
                "pixel_y": round(smoothed.y, 3),
            }

    def _apply_snake_hover_selection_delay(
        self,
        state: OperatorState,
        *,
        head: tuple[int, int],
        now: float,
    ) -> OperatorState:
        """Only apply selectable-option focus after a short hover delay."""
        game = dict(state.header_logo_game or {})
        if not game.get("active"):
            return state
        size = shutil.get_terminal_size((120, 32))
        width = max(72, int(size.columns))
        x, y = head
        x = max(0, min(width - 1, int(x)))
        y = max(0, int(y))

        # Approximate body start from renderer layout (header + rule).
        body_start = 9
        left_width = 22
        candidate: tuple[str, int] | None = None
        if y >= body_start + 1 and x < left_width:
            row = y - (body_start + 1)
            if 0 <= row < len(SECTIONS):
                candidate = ("nav", row)

        if candidate is None:
            game.pop("pending_select_target", None)
            game.pop("pending_select_since", None)
            return state.with_updates(header_logo_game=game)

        delay = max(0.10, min(2.0, float(os.environ.get("ANANTA_TUI_SNAKE_SELECT_DELAY", "0.45"))))
        pending = game.get("pending_select_target")
        since = float(game.get("pending_select_since", now))
        if pending != candidate:
            game["pending_select_target"] = candidate
            game["pending_select_since"] = now
            return state.with_updates(header_logo_game=game, status_message="snake: option anvisiert…")
        if (now - since) < delay:
            return state.with_updates(header_logo_game=game)

        pane, idx = candidate
        game.pop("pending_select_target", None)
        game.pop("pending_select_since", None)
        if pane == "nav":
            return state.with_updates(
                focus=FocusPane.NAVIGATION,
                selected_index=max(0, min(len(SECTIONS) - 1, idx)),
                header_logo_game=game,
                status_message="snake: option gewählt",
            )
        return state.with_updates(header_logo_game=game)

    def _snake_mode_active(self, game: dict[str, object] | None = None) -> bool:
        g = game if game is not None else dict(self.state.header_logo_game or {})
        return bool(g.get("active") and g.get("ui_steering"))

    def _toggle_snake_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if self._snake_mode_active(game):
            game["active"] = True
            game["alive"] = True
            game["ui_steering"] = False
            game["free_mode"] = False
            game["message_mode"] = False
            game["message_draft"] = ""
            game["selection_anchor"] = None
            game["selection_cells"] = []
            game["selection_regions"] = []
            game["selection_frame_mode"] = False
            game["selection_frame_anchor"] = None
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: aus"))
            return
        game["active"] = True
        game["ui_steering"] = True
        game["free_mode"] = True
        if "tutorial_mode" not in game:
            game["tutorial_mode"] = os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1").strip().lower() not in {"0", "false", "no", "off"}
        game["chat_panel_open"] = bool(game.get("chat_panel_open", True))
        game["mouse_follow_enabled"] = bool(game.get("mouse_follow_enabled", self._mouse_capabilities.get("enabled")))
        game["movement_mode"] = "mouse_follow" if bool(game.get("mouse_follow_enabled")) else "keyboard"
        game["message_mode"] = False
        game["message_draft"] = ""
        game["message_style"] = str(game.get("message_style") or "trail")
        game["snake_color"] = str(game.get("snake_color") or "mint")
        game["selection_anchor"] = None
        game["selection_cells"] = []
        game["selection_regions"] = []
        game["selection_frame_mode"] = False
        game["selection_frame_anchor"] = None
        game["last_move"] = time.monotonic()
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=(
                    "snake mode: an | "
                    f"{display_for_action('toggle_tutorial_ai', 'Ctrl+U')}=Auto-Heuristik | "
                    f"{display_for_action('toggle_chat_panel', 'Ctrl+G')}=AI-Chat"
                ),
            )
        )

    def _toggle_tutorial_ai_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("tutorial_mode"))
        game["tutorial_mode"] = not enabled
        if not enabled:
            # Re-enabling tutorial AI: also ensure the game is active
            game["active"] = True
            game["alive"] = True
            label = "an"
        else:
            # Disable visual AI-snake immediately and stop pending visual-AI loops/caches.
            self._disable_visual_ai_snake_runtime(game)
            label = "aus"
        try:
            from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings

            save_tui_chat_settings({"tutorial_mode": bool(game.get("tutorial_mode"))})
        except Exception:
            pass
        self._fire_tutorial_event(game, "tutorial_toggled")
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"visual ai-snake: {label}"))

    # ── T01.02: pause/resume ──────────────────────────────────────────────────

    def _toggle_snake_pause(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game:
            return
        paused = bool(game.get("paused"))
        game["paused"] = not paused
        if not paused:
            # entering pause: zero velocity but keep position
            game["vel_x"] = 0.0
            game["vel_y"] = 0.0
            self._snake_idle_since = time.monotonic()
            status = "snake: pausiert [ Space zum Fortsetzen ]"
        else:
            # resuming
            game["last_move"] = time.monotonic()
            self._snake_idle_since = 0.0
            status = "snake: fortgesetzt"
        self._fire_tutorial_event(game, "snake_paused" if not paused else "any_key")
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    # ── T01.03: terminal size warning exposed via tick (already in renderer) ──

    # ── T02.01: event-driven tutor explanations ───────────────────────────────

    def _fire_score_events(self, game: dict[str, object], *, score: int) -> None:
        prev_score = int(game.get("_prev_score") or 0)
        game["_prev_score"] = score
        milestones = {5: "level_up_5", 10: "level_up_10", 20: "level_up_20"}
        for threshold, event in milestones.items():
            if prev_score < threshold <= score and event not in self._tutor_event_session_used:
                self._queue_tutor_event(game, event)

    def _queue_tutor_event(self, game: dict[str, object], event_key: str) -> None:
        if event_key in self._tutor_event_session_used:
            return
        self._tutor_event_session_used.add(event_key)
        queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
        priority = {"collision_wall": 5, "collision_self": 5, "level_up_20": 4,
                    "level_up_10": 3, "level_up_5": 3, "zone_header": 2,
                    "zone_nav": 2, "zone_content": 2, "zone_detail": 2,
                    "food_eaten": 1}.get(event_key, 1)
        queue.append({"event": event_key, "priority": priority, "at": time.monotonic()})
        # Keep at most 5 entries, drop lowest priority if full
        queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
        game["tutor_event_queue"] = queue[:5]

    def _dequeue_tutor_event(self, game: dict[str, object]) -> str:
        queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
        if not queue:
            return ""
        queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
        event_key = str(queue[0].get("event") or "")
        game["tutor_event_queue"] = queue[1:]
        return event_key

    def _get_tutor_text(self, event_key: str) -> str:
        depth = self._tutor_depth_mode
        try:
            from pathlib import Path
            import yaml as _yaml
            yaml_path = Path(__file__).parent / "snake_tutor_texts.yaml"
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            # try events first, then sections, then idle
            for category in ("events", "sections"):
                bucket = data.get(category, {})
                if event_key in bucket:
                    texts = bucket[event_key]
                    if isinstance(texts, dict):
                        text = str(texts.get(depth) or texts.get("overview") or "")
                        return text.strip().replace("\n", " ").replace("  ", " ")
            return ""
        except Exception:
            return ""

    def _get_idle_tutor_text(self) -> str:
        depth = self._tutor_depth_mode
        try:
            from pathlib import Path
            import yaml as _yaml
            import random
            yaml_path = Path(__file__).parent / "snake_tutor_texts.yaml"
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            idle_list = data.get("idle", [])
            if not idle_list:
                return ""
            entry = random.choice(idle_list)
            if isinstance(entry, dict):
                return str(entry.get(depth) or entry.get("overview") or "").strip().replace("\n", " ").replace("  ", " ")
            return ""
        except Exception:
            return ""

    # ── T02.06: idle comments ─────────────────────────────────────────────────

    def _maybe_fire_idle_comment(self, game: dict[str, object], *, now: float) -> None:
        if bool(game.get("tutor_silent")):
            return
        if not bool(game.get("tutorial_mode")):
            return
        idle_threshold = 8.0
        if self._snake_idle_since == 0.0:
            self._snake_idle_since = now
        idle_duration = now - self._snake_idle_since
        last_idle_at = float(game.get("_last_idle_comment_at") or 0.0)
        if idle_duration >= idle_threshold and (now - last_idle_at) >= 60.0:
            tip = self._get_idle_tutor_text()
            if tip:
                game["_last_idle_comment_at"] = now
                self._inject_tutor_tip(game, tip, source="idle")

    def _inject_tutor_tip(self, game: dict[str, object], tip: str, *, source: str = "event") -> None:
        history: list[dict[str, object]] = list(game.get("tutorial_propose_history") or [])
        history.append({"at": time.monotonic(), "source": source, "target": "content", "text": tip})
        game["tutorial_propose_history"] = history[-10:]
        # T02.04: detect section references and set tutor pointer
        self._maybe_set_tutor_pointer(game, tip)
        # also update s-ai message if it exists
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            snakes = dict(snakes_raw)
            ai = dict(snakes.get("s-ai") or {})
            if ai:
                ai["message"] = tip
                snakes["s-ai"] = ai
                game["snakes"] = snakes

    def _maybe_set_tutor_pointer(self, game: dict[str, object], tip: str) -> None:
        """T02.04: wenn ein Sektionsname im Tip vorkommt, Pointer darauf setzen."""
        from client_surfaces.operator_tui.sections import SECTIONS
        tip_lower = tip.lower()
        for section in SECTIONS:
            if section.id in tip_lower or section.title.lower() in tip_lower:
                game["tutor_pointer"] = {
                    "target": section.id,
                    "expires": time.monotonic() + 2.0,
                    "blink_frame": 0,
                }
                return

    def _tick_tutor_pointer(self, game: dict[str, object], now: float) -> None:
        """T02.04: Pointer-Blink-Frame erhöhen und nach Ablauf löschen."""
        ptr = game.get("tutor_pointer")
        if not isinstance(ptr, dict):
            return
        if now >= float(ptr.get("expires", 0)):
            game.pop("tutor_pointer", None)
            return
        ptr = dict(ptr)
        ptr["blink_frame"] = (int(ptr.get("blink_frame", 0)) + 1) % 6
        game["tutor_pointer"] = ptr

    # ── T02.07: section first-visit explanations ──────────────────────────────

    def _maybe_fire_section_visit_explanation(self, game: dict[str, object], *, section_id: str) -> None:
        if not bool(game.get("tutorial_mode")):
            return
        try:
            from client_surfaces.operator_tui.snake_persistence import mark_section_visited
            is_first = mark_section_visited(section_id)
        except Exception:
            is_first = True
        if not is_first:
            return
        tip = self._get_tutor_text(section_id)
        if not tip:
            return
        self._inject_tutor_tip(game, tip, source=f"section:{section_id}")
        self._fire_tutorial_event(game, "section_visited")

    # ── T04.04: Guided Tour auto-advance ─────────────────────────────────────

    def _tick_guided_tour(self, game: dict[str, object], *, now: float) -> None:
        """T04.04: Guided Mode – navigiert automatisch alle 15s zur nächsten Sektion."""
        ts_raw = game.get("tutorial_state")
        if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
            return
        ts = dict(ts_raw)
        from client_surfaces.operator_tui.sections import SECTIONS
        section_ids = [s.id for s in SECTIONS]
        guided_idx = int(ts.get("guided_section_idx") or 0)
        guided_next_at = float(ts.get("guided_next_at") or 0.0)

        # initialise on first call
        if guided_next_at == 0.0:
            ts["guided_section_idx"] = guided_idx
            ts["guided_next_at"] = now + 15.0
            ts["guided_visited"] = []
            game["tutorial_state"] = ts
            # navigate immediately to first section and explain
            section_id = section_ids[guided_idx % len(section_ids)]
            self._apply_snake_section_target(game, section_id=section_id, now=now)
            tip = self._get_tutor_text(section_id)
            if tip:
                self._inject_tutor_tip(game, tip, source=f"guided:{section_id}")
            return

        if now < guided_next_at:
            return

        guided_visited = list(ts.get("guided_visited") or [])
        current_id = section_ids[guided_idx % len(section_ids)]
        if current_id not in guided_visited:
            guided_visited.append(current_id)

        guided_idx += 1
        if guided_idx >= len(section_ids):
            # tour complete – show summary and disable guided
            ts["guided"] = False
            visited_names = ", ".join(guided_visited)
            summary = f"Tour abgeschlossen! Besuchte Sektionen: {visited_names}. Starte ':tutorial start snake_mode' für den Snake-Modus."
            self._inject_tutor_tip(game, summary, source="guided:summary")
            game["tutorial_state"] = ts
            return

        next_id = section_ids[guided_idx]
        ts["guided_section_idx"] = guided_idx
        ts["guided_next_at"] = now + 15.0
        ts["guided_visited"] = guided_visited
        game["tutorial_state"] = ts
        self._apply_snake_section_target(game, section_id=next_id, now=now)
        tip = self._get_tutor_text(next_id)
        if tip:
            self._inject_tutor_tip(game, tip, source=f"guided:{next_id}")

    def _advance_guided_tour_now(self) -> None:
        """T04.04: Enter-Taste übernimmt – Guided Tour sofort weiterschalten."""
        game = dict(self.state.header_logo_game or {})
        ts_raw = game.get("tutorial_state")
        if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
            return
        ts = dict(ts_raw)
        ts["guided_next_at"] = 0.0
        game["tutorial_state"] = ts
        self._tick_guided_tour(game, now=time.monotonic())
        self._set_state(self.state.with_updates(header_logo_game=game))

    # ── E03.T03: snake role handling ──────────────────────────────────────────

    def _snake_role_for(self, snake_id: str, snapshot: dict[str, object]) -> str:
        if snapshot.get("local"):
            return str(snapshot.get("role") or "player")
        if snake_id == "s-ai":
            return "tutor"
        return str(snapshot.get("role") or "viewer")

    def _snake_cycle_message_style(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        styles = ("trail", "orbit", "ticker")
        current = str(game.get("message_style") or styles[0])
        try:
            idx = styles.index(current)
        except ValueError:
            idx = 0
        next_style = styles[(idx + 1) % len(styles)]
        game["message_style"] = next_style
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake text-style: {next_style}",
            )
        )

    def _snake_cycle_color(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        palette = ("mint", "cyan", "violet", "amber", "rose")
        current = str(game.get("snake_color") or palette[0])
        try:
            idx = palette.index(current)
        except ValueError:
            idx = 0
        next_color = palette[(idx + 1) % len(palette)]
        game["snake_color"] = next_color
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake farbe: {next_color}",
            )
        )

    def _snake_head(self, game: dict[str, object]) -> tuple[int, int] | None:
        snake = game.get("snake") or []
        if not isinstance(snake, list) or not snake:
            return None
        head = snake[0]
        if not isinstance(head, (list, tuple)) or len(head) != 2:
            return None
        return int(head[0]), int(head[1])

    def _snake_toggle_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        head = self._snake_head(game)
        if head is None:
            return
        if bool(game.get("selection_frame_mode")):
            self._snake_commit_frame_selection(game, head=head)
            return
        anchor_raw = game.get("selection_anchor")
        if not isinstance(anchor_raw, (list, tuple)) or len(anchor_raw) != 2:
            game["selection_anchor"] = head
            game["selection_cells"] = []
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake select: start"))
            return
        ax, ay = int(anchor_raw[0]), int(anchor_raw[1])
        hx, hy = head
        min_x, max_x = sorted((ax, hx))
        min_y, max_y = sorted((ay, hy))
        cells = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
        game["selection_anchor"] = None
        game["selection_cells"] = cells

        # Trigger AI explanation for the marked region when tutorial AI is active
        if bool(game.get("active")) and bool(game.get("tutorial_mode")) and cells:
            import time as _time
            now = _time.monotonic()
            # Extract readable text from the selected region
            lines = self._snake_render_plain_lines()
            rows: dict[int, list[int]] = {}
            for cx, cy in cells:
                rows.setdefault(cy, []).append(cx)
            excerpts: list[str] = []
            for row_y in sorted(rows)[:4]:
                if row_y < len(lines):
                    row_text = lines[row_y]
                    xs = sorted(set(rows[row_y]))
                    if xs:
                        chunk = row_text[xs[0]: min(len(row_text), xs[-1] + 1)].strip()
                        if chunk:
                            excerpts.append(chunk)
            excerpt = " | ".join(excerpts)[:200]
            feed = f"Erkläre die Markierung: {excerpt}" if excerpt else "Erkläre den markierten Bereich."
            game["tutorial_user_feed"] = feed
            game["tutorial_ai_local_contact"] = True
            game["tutorial_ai_contact_zone"] = "content"
            # Activate the artifact chat with a synthetic target
            chat_raw = game.get("artifact_chat_state")
            chat = dict(chat_raw) if isinstance(chat_raw, dict) else {}
            messages = [dict(m) for m in (chat.get("messages") or []) if isinstance(m, dict)]
            messages.append({"at": float(now), "source": "system", "text": f"Markierung: {excerpt[:80] or '(Bereich)'}"})
            chat["active_target"] = {"label": excerpt[:40] or "Markierung", "section_id": str(self.state.section_id or ""), "kind": "selection", "path": "", "id": "selection"}
            chat["messages"] = messages[-8:]
            game["artifact_chat_state"] = chat
            # Force immediate AI tip on next tick
            self._tutorial_async_next_refresh_at = 0.0
            self._tutorial_async_tip_future = None

        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake select: {len(cells)} zellen markiert",
            )
        )

    def _snake_toggle_frame_mode(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        head = self._snake_head(game)
        if head is None:
            return
        enabled = bool(game.get("selection_frame_mode"))
        if enabled:
            game["selection_frame_mode"] = False
            game["selection_frame_anchor"] = None
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: aus"))
            return
        game["selection_frame_mode"] = True
        game["selection_frame_anchor"] = head
        if not isinstance(game.get("selection_regions"), list):
            game["selection_regions"] = []
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: an (X setzt Rahmen)"))

    def _snake_clear_visual_marks(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        game["mark_cells"] = []
        game["selection_anchor"] = None
        game["selection_cells"] = []
        game["selection_regions"] = []
        game["selection_frame_mode"] = False
        game["selection_frame_anchor"] = None
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            cleaned: dict[str, dict[str, object]] = {}
            for sid, snap in snakes_raw.items():
                if not isinstance(snap, dict):
                    continue
                s = dict(snap)
                s["mark_cells"] = []
                s["selection_cells"] = []
                s["selection_regions"] = []
                cleaned[str(sid)] = s
            game["snakes"] = cleaned
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake marks: ausgeblendet"))

    def _snake_commit_frame_selection(self, game: dict[str, object], *, head: tuple[int, int]) -> None:
        anchor_raw = game.get("selection_frame_anchor")
        if not isinstance(anchor_raw, (list, tuple)) or len(anchor_raw) != 2:
            game["selection_frame_anchor"] = head
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: anchor gesetzt"))
            return
        ax, ay = int(anchor_raw[0]), int(anchor_raw[1])
        hx, hy = head
        min_x, max_x = sorted((ax, hx))
        min_y, max_y = sorted((ay, hy))
        region_cells = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
        existing_raw = game.get("selection_cells") or []
        existing = {
            (int(c[0]), int(c[1]))
            for c in existing_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        }
        existing.update(region_cells)
        game["selection_cells"] = sorted(existing)
        regions_raw = game.get("selection_regions")
        regions = list(regions_raw) if isinstance(regions_raw, list) else []
        regions.append((min_x, min_y, max_x, max_y))
        game["selection_regions"] = regions
        game["selection_frame_anchor"] = head
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake frame: +{len(region_cells)} zellen ({len(regions)} rahmen)",
            )
        )

    def _snake_render_plain_lines(self) -> list[str]:
        rendered_current = str(getattr(self, "_rendered_text", "") or "")
        if rendered_current.strip():
            return [_ANSI_STRIP.sub("", line) for line in rendered_current.splitlines()]
        game = dict(self.state.header_logo_game or {})
        if game.get("free_mode"):
            game["free_mode"] = False
        temp_state = self.state.with_updates(header_logo_game=game)
        size = shutil.get_terminal_size((120, 32))
        rendered = render_operator_shell(temp_state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
        return [_ANSI_STRIP.sub("", line) for line in rendered.splitlines()]

    def _snake_copy_selection(self) -> None:
        try:
            game = dict(self.state.header_logo_game or {})
            self._snake_copy_selection_to_game(game)
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message=str(game.get("_copy_status_message") or "snake copy"),
                )
            )
        except Exception:
            self._set_state(self.state.with_updates(status_message="copy: Fehler (TUI stabil)"))

    def _copy_ask_answer_to_game(self, game: dict[str, object]) -> None:
        """In :ask mode, copy the raw AI answer from chat_state directly.

        Terminal-native copy strips trailing spaces and smashes lines together.
        This extracts the answer text before any screen-rendering so spaces
        are always preserved.
        """
        from client_surfaces.operator_tui.chat_state import get_active_channel
        question = str(game.get("tutor_ask_question") or "").strip()
        chat = game.get("chat_state") if isinstance(game.get("chat_state"), dict) else {}
        channel = (get_active_channel(chat) or {}) if chat else {}
        messages = list(channel.get("messages") or []) if isinstance(channel, dict) else []
        answer_text = ""
        sender_label = "AI-Snake"
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if str(msg.get("sender_kind") or "") == "ai":
                t = str(msg.get("text") or "").strip()
                if t:
                    answer_text = t
                    sender_label = str(msg.get("sender_id") or "AI-Snake")
                    break
        # Also check streaming partial
        partial = str(game.get("llm_streaming_partial") or "").strip()
        if partial:
            answer_text = partial
        lines_out: list[str] = []
        if question:
            lines_out.append(f"Frage: {question}")
            lines_out.append("")
        if answer_text:
            lines_out.append(f"{sender_label}:")
            lines_out.append(answer_text)
        else:
            lines_out.append("(noch keine Antwort)")
        copied = "\n".join(lines_out).strip()
        game["clipboard"] = copied
        if copied:
            self._copy_to_clipboard_bg(copied)
        game["_copy_status_message"] = "ask copy: Antwort in Zwischenablage"

    def _snake_copy_selection_to_game(self, game: dict[str, object]) -> None:
        # :ask mode: copy the raw AI answer directly so spaces are preserved
        if str(game.get("tutor_ask_question") or "").strip():
            # Only bypass if no drag selection is active (user may want to select a snippet)
            _has_drag = bool(game.get("mouse_selection_range") or game.get("mouse_selection_committed_ranges"))
            if not _has_drag:
                self._copy_ask_answer_to_game(game)
                return

        # Mouse drag selection takes priority over cell-based snake selection
        _active_range = game.get("mouse_selection_range")
        if _active_range and isinstance(_active_range, dict):
            _all_ranges: list[dict] = [_active_range]
            for _c in (game.get("mouse_selection_committed_ranges") or []):
                if isinstance(_c, dict):
                    _all_ranges.append(_c)
            try:
                _plain_lines = self._snake_render_plain_lines()
            except Exception:
                game["_copy_status_message"] = "copy: Render-Fehler"
                return
            _chunks: list[str] = []
            for _r in _all_ranges:
                try:
                    _sx = int(_r.get("start_x", 0))
                    _sy = int(_r.get("start_y", 0))
                    _ex = int(_r.get("end_x", 0))
                    _ey = int(_r.get("end_y", 0))
                    _mode = str(_r.get("mode", "linear"))
                    if _sy > _ey or (_sy == _ey and _sx > _ex):
                        _sx, _ex = _ex, _sx
                        _sy, _ey = _ey, _sy
                    for _ly in range(max(0, _sy), min(_ey + 1, len(_plain_lines))):
                        _row = _plain_lines[_ly]
                        if _mode == "block":
                            _x1, _x2 = sorted([_sx, _ex])
                        elif _ly == _sy and _ly == _ey:
                            _x1, _x2 = sorted([_sx, _ex])
                        elif _ly == _sy:
                            _x1, _x2 = _sx, len(_row)
                        elif _ly == _ey:
                            _x1, _x2 = 0, _ex + 1
                        else:
                            _x1, _x2 = 0, len(_row)
                        _chunks.append(_row[_x1:_x2])
                except Exception:
                    continue
            copied = "\n".join(_chunks).rstrip("\n")
            game["clipboard"] = copied
            if copied:
                game["message"] = copied
                self._copy_to_clipboard_bg(copied)
            game["_copy_status_message"] = "copy: Zwischenablage"
            return

        cells_raw = game.get("selection_cells") or []
        cells = [
            (int(c[0]), int(c[1]))
            for c in cells_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            game["_copy_status_message"] = "snake copy: keine auswahl"
            return
        try:
            lines = self._snake_render_plain_lines()
        except Exception:
            game["_copy_status_message"] = "copy: Render-Fehler"
            return
        by_row: dict[int, list[int]] = {}
        for x, y in cells:
            by_row.setdefault(y, []).append(x)
        chunks: list[str] = []
        for y in sorted(by_row.keys()):
            if y < 0 or y >= len(lines):
                continue
            row = lines[y]
            xs = by_row[y]
            if not xs:
                continue
            x_sorted = sorted(set(xs))
            parts: list[str] = []
            seg_start = x_sorted[0]
            seg_end = x_sorted[0]
            for x in x_sorted[1:]:
                if x == seg_end + 1:
                    seg_end = x
                    continue
                if seg_start < len(row):
                    parts.append(row[seg_start : min(len(row), seg_end + 1)])
                seg_start = x
                seg_end = x
            if seg_start < len(row):
                parts.append(row[seg_start : min(len(row), seg_end + 1)])
            if parts:
                chunks.append(" | ".join(parts))
        copied = "\n".join(chunks).rstrip("\n")
        game["clipboard"] = copied
        if copied:
            game["message"] = copied
            self._copy_to_clipboard_bg(copied)
        game["_copy_status_message"] = "snake copy: Zwischenablage"

    def _copy_to_clipboard_bg(self, text: str) -> None:
        """Fire-and-forget clipboard copy in a daemon thread to avoid blocking the UI."""
        if not text:
            return
        threading.Thread(
            target=self._copy_to_system_clipboard,
            args=(text,),
            daemon=True,
            name="tui-clipboard-bg",
        ).start()

    def _copy_to_system_clipboard(self, text: str) -> bool:
        if not text:
            return False
        clipboard_text = clipboard_safe_text(text)
        if not clipboard_text:
            return False
        commands = [
            ["clip.exe"],
            ["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard"],
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard"],
            ["pbcopy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
        for command in commands:
            try:
                completed = process.run(
                    command,
                    input=clipboard_text,
                    text=True,
                    stdout=process.DEVNULL,
                    stderr=process.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
            except (OSError, process.SubprocessError):
                continue
            if completed.returncode == 0:
                return True
        return False

    def _snake_replace_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        cells_raw = game.get("selection_cells") or []
        cells = [
            (int(c[0]), int(c[1]))
            for c in cells_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake replace: keine auswahl"))
            return
        if self.state.mode is not OperatorMode.COMMAND:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: nur im editierbaren command-feld",
                )
            )
            return
        lines = self._snake_render_plain_lines()
        if not lines:
            return
        command_row = len(lines) - 2
        ys = {y for _, y in cells}
        if ys != {command_row}:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: auswahl muss in der command-zeile liegen",
                )
            )
            return
        replacement = str(game.get("message") or game.get("clipboard") or "")
        if not replacement:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: keine message/clipboard vorhanden",
                )
            )
            return
        xs = sorted(x for x, _ in cells)
        min_x = min(xs)
        max_x = max(xs)
        # command line has one visible prefix char (":" in command mode)
        start = max(0, min_x - 1)
        end = max(start, max_x - 1)
        cmd = self.state.command_line
        if start > len(cmd):
            start = len(cmd)
        end = min(len(cmd) - 1, end) if cmd else -1
        if end >= start:
            new_cmd = cmd[:start] + replacement + cmd[end + 1 :]
        else:
            new_cmd = cmd[:start] + replacement + cmd[start:]
        self._command_buffer = new_cmd
        game["selection_anchor"] = None
        game["selection_cells"] = []
        self._set_state(
            self.state.with_updates(
                command_line=new_cmd,
                header_logo_game=game,
                status_message="snake replace: command-feld ersetzt",
            )
        )

    def _snake_message_mode_active(self) -> bool:
        game = dict(self.state.header_logo_game or {})
        return bool(game.get("message_mode"))

    def _toggle_snake_message_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["active"] = True
        game["ui_steering"] = True
        if game.get("message_mode"):
            game["message_mode"] = False
            game["message_draft"] = ""
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: abgebrochen"))
            return
        game["message_mode"] = True
        game["message_draft"] = str(game.get("message", ""))
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: eingeben + Enter speichern"))

    def _snake_message_append(self, text: str) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        draft = str(game.get("message_draft", ""))
        game["message_draft"] = (draft + text)[:200]
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: tippen..."))

    def _snake_message_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        draft = str(game.get("message_draft", ""))
        game["message_draft"] = draft[:-1]
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: tippen..."))

    def _snake_cancel_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        game["message_mode"] = False
        game["message_draft"] = ""
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: abgebrochen"))

    def _snake_commit_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        message = str(game.get("message_draft", "")).strip()
        if message.lower().startswith("/template "):
            template = message[10:].strip()
            if template:
                game["tutorial_prompt_template"] = template
                game["message"] = f"template set ({len(template)} chars)"
                status_message = "snake template: gespeichert"
            else:
                status_message = "snake template: leer, ignoriert"
        else:
            game["message"] = message
            game["tutorial_user_feed"] = message
            if message:
                target = str(game.get("tutorial_ai_contact_zone") or self.state.section_id or "content")
                history_raw = game.get("tutorial_propose_history")
                history = [dict(item) for item in history_raw if isinstance(item, dict)] if isinstance(history_raw, list) else []
                history.append(
                    {
                        "at": float(time.monotonic()),
                        "source": "user",
                        "target": target,
                        "text": message,
                    }
                )
                game["tutorial_propose_history"] = history[-8:]
                # Force a fresh AI response after a new user question.
                self._tutorial_worker_cache = (0.0, "")
                self._tutorial_llm_cache = (0.0, "")
            status_message = "snake message/feed: gespeichert"
        game["message_mode"] = False
        game["message_draft"] = ""
        self._save_snake_message_config(message)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status_message))

    def _save_snake_message_config(self, message: str) -> None:
        cfg_dir = Path.home() / ".config" / "ananta"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "snake-config.json"
        game = dict(self.state.header_logo_game or {})
        payload = {
            "snake_message": message,
            "tutorial_user_feed": str(game.get("tutorial_user_feed") or message),
            "tutorial_prompt_template": str(game.get("tutorial_prompt_template") or ""),
            "updated_at": int(time.time()),
        }
        cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_snake_message_config(self) -> dict[str, object]:
        cfg_file = Path.home() / ".config" / "ananta" / "snake-config.json"
        if not cfg_file.exists():
            return {}
        try:
            payload = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _snake_immediate_brake(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game:
            return
        game["vel_x"] = 0.0
        game["vel_y"] = 0.0
        game["accum_x"] = 0.0
        game["accum_y"] = 0.0
        game["next_direction"] = (0, 0)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake: sofortstopp"))

    def _snake_escape_target(
        self,
        *,
        nx: int,
        ny: int,
        hx: int,
        hy: int,
        board_w: int,
        board_h: int,
        gaps: object,
    ) -> FocusPane | None:
        g = self._ensure_snake_escape_gaps(gaps, board_w=board_w, board_h=board_h, seed=0)
        right_gap = int(g.get("right", 1))
        if nx >= board_w and abs(hy - right_gap) <= 1:
            return FocusPane.NAVIGATION
        if ny >= board_h:
            bottom_nav = int(g.get("bottom_nav", board_w // 5))
            bottom_content = int(g.get("bottom_content", board_w // 2))
            bottom_detail = int(g.get("bottom_detail", (board_w * 4) // 5))
            if abs(hx - bottom_nav) <= 1:
                return FocusPane.NAVIGATION
            if abs(hx - bottom_content) <= 1:
                return FocusPane.CONTENT
            if abs(hx - bottom_detail) <= 1:
                return FocusPane.DETAIL
        return None

    def _apply_snake_escape(
        self,
        game: dict[str, object],
        *,
        target: FocusPane,
        now: float,
        board_h: int,
    ) -> None:
        game["active"] = True
        game["ui_steering"] = True
        game["escaped_to"] = target.value
        game["last_move"] = now
        if target is FocusPane.NAVIGATION:
            nav_idx = min(len(SECTIONS) - 1, max(0, int((int(game.get("moves", 0)) + board_h) % max(1, len(SECTIONS)))))
            self.state = self.state.with_updates(
                focus=FocusPane.NAVIGATION,
                selected_index=nav_idx,
                header_logo_game=game,
                status_message="snake: ausgebrochen nach NAV",
            )
            return
        selected = max(0, min(999999, int(self.state.selected_index)))
        self.state = self.state.with_updates(
            focus=target,
            selected_index=selected,
            header_logo_game=game,
            status_message=f"snake: ausgebrochen nach {target.value}",
        )

    def _apply_snake_section_target(self, game: dict[str, object], *, section_id: str, now: float) -> None:
        section_ids = [s.id for s in SECTIONS]
        if section_id not in section_ids:
            return
        idx = section_ids.index(section_id)
        game["active"] = True
        game["ui_steering"] = True
        game["escaped_to"] = "navigation"
        game["last_move"] = now
        next_state = self.state.with_updates(
            focus=FocusPane.NAVIGATION,
            selected_index=idx,
            section_id=section_id,
            header_logo_game=game,
            status_message=f"snake: section {section_id}",
        )
        self.state = load_active_section(next_state, self._registry)

    def _apply_snake_ui_controls(
        self,
        state: OperatorState,
        *,
        head: tuple[int, int],
        board_w: int,
        board_h: int,
    ) -> OperatorState:
        game = dict(state.header_logo_game or {})
        if not game.get("ui_steering"):
            return state
        x, y = head
        x = max(0, min(board_w - 1, x))
        y = max(0, min(board_h - 1, y))
        third = max(1, board_w // 3)

        # Top-center zone acts as "input field focus" (command line mode).
        if y <= 1 and third <= x < (third * 2):
            return state.with_updates(mode=OperatorMode.COMMAND, command_line=state.command_line)

        if x < third:
            nav_idx = min(len(SECTIONS) - 1, max(0, round((y / max(1, board_h - 1)) * max(0, len(SECTIONS) - 1))))
            return state.with_updates(focus=FocusPane.NAVIGATION, selected_index=nav_idx, mode=OperatorMode.NORMAL)
        if x < (third * 2):
            content_idx = max(0, round((y / max(1, board_h - 1)) * 8))
            return state.with_updates(focus=FocusPane.CONTENT, selected_index=content_idx, mode=OperatorMode.NORMAL)
        detail_idx = max(0, round((y / max(1, board_h - 1)) * 8))
        return state.with_updates(focus=FocusPane.DETAIL, selected_index=detail_idx, mode=OperatorMode.NORMAL)

    def _compute_control_boxes(
        self,
        board_w: int,
        board_h: int,
    ) -> list[dict[str, object]]:
        return []

    def _box_hit_target(
        self,
        head: tuple[int, int],
        boxes: list[dict[str, object]],
    ) -> FocusPane | str | None:
        _ = (head, boxes)
        return None

    def _ensure_snake_escape_gaps(
        self,
        gaps: object,
        *,
        board_w: int,
        board_h: int,
        seed: int,
    ) -> dict[str, int]:
        if isinstance(gaps, dict):
            try:
                keys = ("right", "bottom_nav", "bottom_content", "bottom_detail")
                parsed = {k: int(gaps[k]) for k in keys}
                if 1 <= parsed["right"] <= board_h - 2:
                    for key in ("bottom_nav", "bottom_content", "bottom_detail"):
                        parsed[key] = max(1, min(board_w - 2, parsed[key]))
                    return parsed
            except Exception:
                pass
        return self._compute_snake_escape_gaps(board_w, board_h, seed=seed)

    def _compute_snake_escape_gaps(self, board_w: int, board_h: int, *, seed: int) -> dict[str, int]:
        usable_w = max(3, board_w - 2)
        right_gap = 1 + ((seed // 3) % max(1, board_h - 2))
        nav = 1 + ((seed // 5) % max(1, usable_w // 3))
        content = max(2, min(board_w - 2, board_w // 2 + ((seed // 7) % 3) - 1))
        detail = max(2, min(board_w - 2, board_w - 2 - ((seed // 11) % max(1, usable_w // 3))))
        if detail <= content:
            detail = min(board_w - 2, content + 2)
        if content <= nav:
            content = min(board_w - 2, nav + 2)
        return {
            "right": right_gap,
            "bottom_nav": nav,
            "bottom_content": content,
            "bottom_detail": detail,
        }

    def _spawn_snake_food(
        self,
        board_w: int,
        board_h: int,
        snake: list[tuple[int, int]],
        seed: int,
    ) -> tuple[int, int]:
        occupied = set(snake)
        free = [(x, y) for y in range(board_h) for x in range(board_w) if (x, y) not in occupied]
        if not free:
            return snake[-1] if snake else (0, 0)
        idx = (seed * 17 + board_w * 13 + board_h * 7) % len(free)
        return free[idx]
    def _disable_visual_ai_snake_runtime(self, game: dict[str, object]) -> None:
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            snakes = dict(snakes_raw)
            snakes.pop("s-ai", None)
            game["snakes"] = snakes
        game["tutorial_ai_local_contact"] = False
        game["tutorial_ai_contact_zone"] = ""
        game["tutorial_ai_contact_at"] = 0.0
        self._tutorial_async_next_refresh_at = 0.0
        pending = getattr(self, "_tutorial_async_tip_future", None)
        if pending is not None:
            try:
                pending.cancel()
            except Exception:
                pass
        self._tutorial_async_tip_future = None
        self._tutorial_worker_cache = (0.0, "")
        self._tutorial_llm_cache = (0.0, "")
        self._tutorial_worker_target_hint = ""
        self._tutorial_last_tip_text = ""
