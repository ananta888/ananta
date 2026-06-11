from __future__ import annotations

import math
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow
from client_surfaces.operator_tui.models import FocusPane, OperatorState

if TYPE_CHECKING:
    from client_surfaces.operator_tui.snake_ops_mixin import SnakeOpsMixin


def update_demo_remote_snakes(
    tui: SnakeOpsMixin,
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
        intent_level = str((tui.state.header_logo_game or {}).get("artifact_intent_confidence") or "none")
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


def apply_snake_hover_selection_delay(
    tui: SnakeOpsMixin,
    state: OperatorState,
    *,
    head: tuple[int, int],
    now: float,
) -> OperatorState:
    game = dict(state.header_logo_game or {})
    if not game.get("active"):
        return state
    size = shutil.get_terminal_size((120, 32))
    width = max(72, int(size.columns))
    x, y = head
    x = max(0, min(width - 1, int(x)))
    y = max(0, int(y))

    body_start = 9
    left_width = 22
    candidate: tuple[str, int] | None = None
    if y >= body_start + 1 and x < left_width:
        row = y - (body_start + 1)
        from client_surfaces.operator_tui.sections import SECTIONS
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


def snake_mode_active(tui: SnakeOpsMixin, game: dict[str, object] | None = None) -> bool:
    g = game if game is not None else dict(tui.state.header_logo_game or {})
    return bool(g.get("active") and g.get("ui_steering"))


def toggle_snake_mode(tui: SnakeOpsMixin) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if snake_mode_active(tui, game):
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
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="snake mode: aus"))
        return
    game["active"] = True
    game["ui_steering"] = True
    game["free_mode"] = True
    if "tutorial_mode" not in game:
        game["tutorial_mode"] = os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1").strip().lower() not in {"0", "false", "no", "off"}
    game["chat_panel_open"] = bool(game.get("chat_panel_open", True))
    game["mouse_follow_enabled"] = bool(game.get("mouse_follow_enabled", tui._mouse_capabilities.get("enabled")))
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
    from client_surfaces.operator_tui.keybindings_config import display_for_action
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message=(
                "snake mode: an | "
                f"{display_for_action('toggle_tutorial_ai', 'Ctrl+U')}=Auto-Heuristik | "
                f"{display_for_action('toggle_chat_panel', 'Ctrl+G')}=AI-Chat"
            ),
        )
    )


def toggle_tutorial_ai_mode(tui: SnakeOpsMixin) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    enabled = bool(game.get("tutorial_mode"))
    game["tutorial_mode"] = not enabled
    if not enabled:
        game["active"] = True
        game["alive"] = True
        label = "an"
    else:
        tui._disable_visual_ai_snake_runtime(game)
        label = "aus"
    try:
        from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings
        save_tui_chat_settings({"tutorial_mode": bool(game.get("tutorial_mode"))})
    except Exception:
        pass
    tui._fire_tutorial_event(game, "tutorial_toggled")
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"visual ai-snake: {label}"))


def toggle_snake_pause(tui: SnakeOpsMixin) -> None:
    game = dict(tui.state.header_logo_game or {})
    if not game:
        return
    paused = bool(game.get("paused"))
    game["paused"] = not paused
    if not paused:
        game["vel_x"] = 0.0
        game["vel_y"] = 0.0
        tui._snake_idle_since = time.monotonic()
        status = "snake: pausiert [ Space zum Fortsetzen ]"
    else:
        game["last_move"] = time.monotonic()
        tui._snake_idle_since = 0.0
        status = "snake: fortgesetzt"
    tui._fire_tutorial_event(game, "snake_paused" if not paused else "any_key")
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=status))


def fire_score_events(tui: SnakeOpsMixin, game: dict[str, object], *, score: int) -> None:
    prev_score = int(game.get("_prev_score") or 0)
    game["_prev_score"] = score
    milestones = {5: "level_up_5", 10: "level_up_10", 20: "level_up_20"}
    for threshold, event in milestones.items():
        if prev_score < threshold <= score and event not in tui._tutor_event_session_used:
            queue_tutor_event(tui, game, event)


def queue_tutor_event(tui: SnakeOpsMixin, game: dict[str, object], event_key: str) -> None:
    if event_key in tui._tutor_event_session_used:
        return
    tui._tutor_event_session_used.add(event_key)
    queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
    priority = {"collision_wall": 5, "collision_self": 5, "level_up_20": 4,
                "level_up_10": 3, "level_up_5": 3, "zone_header": 2,
                "zone_nav": 2, "zone_content": 2, "zone_detail": 2,
                "food_eaten": 1}.get(event_key, 1)
    queue.append({"event": event_key, "priority": priority, "at": time.monotonic()})
    queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
    game["tutor_event_queue"] = queue[:5]


def dequeue_tutor_event(tui: SnakeOpsMixin, game: dict[str, object]) -> str:
    queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
    if not queue:
        return ""
    queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
    event_key = str(queue[0].get("event") or "")
    game["tutor_event_queue"] = queue[1:]
    return event_key


def get_tutor_text(tui: SnakeOpsMixin, event_key: str) -> str:
    depth = tui._tutor_depth_mode
    try:
        from pathlib import Path
        import yaml as _yaml
        yaml_path = Path(__file__).parent / "snake_tutor_texts.yaml"
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
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


def get_idle_tutor_text(tui: SnakeOpsMixin) -> str:
    depth = tui._tutor_depth_mode
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


def maybe_fire_idle_comment(tui: SnakeOpsMixin, game: dict[str, object], *, now: float) -> None:
    if bool(game.get("tutor_silent")):
        return
    if not bool(game.get("tutorial_mode")):
        return
    idle_threshold = 8.0
    if tui._snake_idle_since == 0.0:
        tui._snake_idle_since = now
    idle_duration = now - tui._snake_idle_since
    last_idle_at = float(game.get("_last_idle_comment_at") or 0.0)
    if idle_duration >= idle_threshold and (now - last_idle_at) >= 60.0:
        tip = get_idle_tutor_text(tui)
        if tip:
            game["_last_idle_comment_at"] = now
            inject_tutor_tip(tui, game, tip, source="idle")


def inject_tutor_tip(tui: SnakeOpsMixin, game: dict[str, object], tip: str, *, source: str = "event") -> None:
    history: list[dict[str, object]] = list(game.get("tutorial_propose_history") or [])
    history.append({"at": time.monotonic(), "source": source, "target": "content", "text": tip})
    game["tutorial_propose_history"] = history[-10:]
    maybe_set_tutor_pointer(tui, game, tip)
    snakes_raw = game.get("snakes")
    if isinstance(snakes_raw, dict):
        snakes = dict(snakes_raw)
        ai = dict(snakes.get("s-ai") or {})
        if ai:
            ai["message"] = tip
            snakes["s-ai"] = ai
            game["snakes"] = snakes


def maybe_set_tutor_pointer(tui: SnakeOpsMixin, game: dict[str, object], tip: str) -> None:
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


def tick_tutor_pointer(tui: SnakeOpsMixin, game: dict[str, object], now: float) -> None:
    ptr = game.get("tutor_pointer")
    if not isinstance(ptr, dict):
        return
    if now >= float(ptr.get("expires", 0)):
        game.pop("tutor_pointer", None)
        return
    ptr = dict(ptr)
    ptr["blink_frame"] = (int(ptr.get("blink_frame", 0)) + 1) % 6
    game["tutor_pointer"] = ptr


def maybe_fire_section_visit_explanation(tui: SnakeOpsMixin, game: dict[str, object], *, section_id: str) -> None:
    if not bool(game.get("tutorial_mode")):
        return
    try:
        from client_surfaces.operator_tui.snake_persistence import mark_section_visited
        is_first = mark_section_visited(section_id)
    except Exception:
        is_first = True
    if not is_first:
        return
    tip = get_tutor_text(tui, section_id)
    if not tip:
        return
    inject_tutor_tip(tui, game, tip, source=f"section:{section_id}")
    tui._fire_tutorial_event(game, "section_visited")


def tick_guided_tour(tui: SnakeOpsMixin, game: dict[str, object], *, now: float) -> None:
    ts_raw = game.get("tutorial_state")
    if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
        return
    ts = dict(ts_raw)
    from client_surfaces.operator_tui.sections import SECTIONS
    section_ids = [s.id for s in SECTIONS]
    guided_idx = int(ts.get("guided_section_idx") or 0)
    guided_next_at = float(ts.get("guided_next_at") or 0.0)

    if guided_next_at == 0.0:
        ts["guided_section_idx"] = guided_idx
        ts["guided_next_at"] = now + 15.0
        ts["guided_visited"] = []
        game["tutorial_state"] = ts
        section_id = section_ids[guided_idx % len(section_ids)]
        tui._apply_snake_section_target(game, section_id=section_id, now=now)
        tip = get_tutor_text(tui, section_id)
        if tip:
            inject_tutor_tip(tui, game, tip, source=f"guided:{section_id}")
        return

    if now < guided_next_at:
        return

    guided_visited = list(ts.get("guided_visited") or [])
    current_id = section_ids[guided_idx % len(section_ids)]
    if current_id not in guided_visited:
        guided_visited.append(current_id)

    guided_idx += 1
    if guided_idx >= len(section_ids):
        ts["guided"] = False
        visited_names = ", ".join(guided_visited)
        summary = f"Tour abgeschlossen! Besuchte Sektionen: {visited_names}. Starte ':tutorial start snake_mode' für den Snake-Modus."
        inject_tutor_tip(tui, game, summary, source="guided:summary")
        game["tutorial_state"] = ts
        return

    next_id = section_ids[guided_idx]
    ts["guided_section_idx"] = guided_idx
    ts["guided_next_at"] = now + 15.0
    ts["guided_visited"] = guided_visited
    game["tutorial_state"] = ts
    tui._apply_snake_section_target(game, section_id=next_id, now=now)
    tip = get_tutor_text(tui, next_id)
    if tip:
        inject_tutor_tip(tui, game, tip, source=f"guided:{next_id}")


def advance_guided_tour_now(tui: SnakeOpsMixin) -> None:
    game = dict(tui.state.header_logo_game or {})
    ts_raw = game.get("tutorial_state")
    if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
        return
    ts = dict(ts_raw)
    ts["guided_next_at"] = 0.0
    game["tutorial_state"] = ts
    tui._tick_guided_tour(game, now=time.monotonic())
    tui._set_state(tui.state.with_updates(header_logo_game=game))
