"""HeaderSnakeMixin — header snake management methods.

Contains: _header_snake_enabled, _default_header_snake, _activate_header_snake,
          _deactivate_header_snake, _try_header_snake_direction
"""
from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.ai_snake_follow import make_follow_state
from client_surfaces.operator_tui.models import OperatorMode, OperatorState

_TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT = (
    "You are tutorial-snake guidance.\n"
    "Priority: {priority}\n"
    "User feed: {user_feed}\n"
    "Contact zone: {contact_zone}\n"
    "Respond with one immediate actionable hint (max 180 chars)."
)


class HeaderSnakeMixin:
    """Mixin providing header snake enable/disable and direction handling."""

    def _header_snake_enabled(self) -> bool:
        return os.environ.get("ANANTA_TUI_HEADER_SNAKE", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _default_header_snake(self) -> dict[str, object]:
        cfg = self._load_snake_message_config()
        board_w, board_h = 18, 6
        snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        gaps = self._compute_snake_escape_gaps(board_w, board_h, seed=int(time.time() * 1000))
        _tutorial_on = os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1").strip().lower() not in {"0", "false", "no", "off"}
        return {
            "active": _tutorial_on,  # auto-start when tutorial AI is enabled
            "alive": True,
            "ui_steering": False,
            "free_mode": False,
            "local_snake_id": "s1",
            "pseudonym": os.environ.get("ANANTA_TUI_SNAKE_PSEUDONYM", "local-snake"),
            "oidc_provider": os.environ.get("ANANTA_TUI_SNAKE_OIDC_PROVIDER", "local"),
            "board_w": board_w,
            "board_h": board_h,
            "snake": snake,
            "trail_path": list(snake),
            "mark_cells": [],
            "selection_anchor": None,
            "selection_cells": [],
            "selection_regions": [],
            "selection_frame_mode": False,
            "selection_frame_anchor": None,
            "clipboard": "",
            "message": str(cfg.get("snake_message") or ""),
            "tutorial_user_feed": str(cfg.get("tutorial_user_feed") or ""),
            "tutorial_prompt_template": str(
                cfg.get("tutorial_prompt_template")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_PROMPT_TEMPLATE")
                or _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT
            ),
            "message_style": "trail",
            "snake_color": "mint",
            "movement_mode": "mouse_follow" if bool(self._mouse_capabilities.get("enabled")) else "keyboard",
            "mouse_follow_enabled": bool(self._mouse_capabilities.get("enabled")),
            "mouse_state": {},
            "mouse_target": None,
            "artifact_intent_confidence": "none",
            "artifact_intent_score": 0.0,
            "artifact_intent_reason": "",
            "artifact_intent_target": None,
            "ai_snake_mode": "lurking_follow",
            "ai_snake_provider_preference": os.environ.get("ANANTA_TUI_AI_SNAKE_PROVIDER", "lmstudio"),
            "ai_snake_provider_model": os.environ.get("ANANTA_TUI_AI_SNAKE_MODEL", "ananta-smoke"),
            "ai_snake_provider_cloud_allowed": os.environ.get("ANANTA_TUI_AI_SNAKE_CLOUD_ALLOWED", "0").strip().lower() in {"1", "true", "yes", "on"},
            "ai_snake_provider_max_latency_ms": max(250, int(os.environ.get("ANANTA_TUI_AI_SNAKE_MAX_LATENCY_MS", "2000"))),
            "ai_snake_prediction": {},
            "ai_snake_debug": {},
            "ai_snake_runtime_status": "idle",
            "ai_training_context_released": False,
            "ai_snake_follow_state": make_follow_state(ai_position=(3, 3), mode="lurking_follow"),
            "artifact_target_cell": None,
            "tutorial_ai_target_mode": "follow_user",
            "tutorial_ai_target_hint": "follow",
            "artifact_chat_state": {
                "active_target": None,
                "messages": [],
                "pending_request": "",
                "backend_source": "",
                "error": "",
            },
            "trail_window": max(1, min(120, int(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_WINDOW", "10")))),
            "trail_speed": max(0.2, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_SPEED", "8.0")))),
            "tutorial_mode": _tutorial_on,
            "snakes": {},
            "direction": (1, 0),
            "next_direction": (1, 0),
            "vel_x": 10.0,
            "vel_y": 0.0,
            "accum_x": 0.0,
            "accum_y": 0.0,
            "food": (12, 3),
            "gaps": gaps,
            "score": 0,
            "moves": 0,
            "last_move": time.monotonic(),
        }

    def _activate_header_snake(self, state: OperatorState) -> OperatorState:
        if not self._header_snake_enabled():
            return state
        game = dict(state.header_logo_game or self._default_header_snake())
        board_w = max(6, int(game.get("board_w", 18)))
        board_h = max(4, int(game.get("board_h", 6)))
        game["gaps"] = self._ensure_snake_escape_gaps(
            game.get("gaps"),
            board_w=board_w,
            board_h=board_h,
            seed=int(time.time() * 1000),
        )
        game["active"] = True
        game["ui_steering"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        game["last_move"] = time.monotonic()
        return state.with_updates(header_logo_game=game)

    def _deactivate_header_snake(self, state: OperatorState) -> OperatorState:
        game = dict(state.header_logo_game or {})
        if not game:
            return state
        if game.get("ui_steering"):
            return state.with_updates(header_logo_game=game)
        game["active"] = False
        return state.with_updates(header_logo_game=game)

    def _try_header_snake_direction(self, direction: tuple[int, int]) -> bool:
        game = dict(self.state.header_logo_game or {})
        if self.state.mode is OperatorMode.COMMAND and not game.get("ui_steering"):
            return False
        if not self._header_snake_enabled():
            return False
        steering = self._snake_mode_active(game)
        if not steering:
            return False
        if not game.get("active", False):
            game["active"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        accel = max(1.0, min(20.0, float(os.environ.get("ANANTA_TUI_HEADER_SNAKE_ACCEL", "3.0"))))
        max_speed = max(6.0, min(120.0, float(os.environ.get("ANANTA_TUI_HEADER_SNAKE_MAX_SPEED", "70"))))
        vx = float(game.get("vel_x", 10.0))
        vy = float(game.get("vel_y", 0.0))
        dx, dy = direction
        if dx:
            vx += accel * dx
            vy *= 0.15
            if abs(vx) < 4.0:
                vx = 4.0 * dx
        if dy:
            vy += accel * dy
            vx *= 0.15
            if abs(vy) < 4.0:
                vy = 4.0 * dy
        if abs(vx) < 0.1 and abs(vy) < 0.1:
            vx = 4.0 * dx
            vy = 4.0 * dy
        vx = max(-max_speed, min(max_speed, vx))
        vy = max(-max_speed, min(max_speed, vy))
        game["vel_x"] = vx
        game["vel_y"] = vy
        game["next_direction"] = direction
        self._set_state(self.state.with_updates(header_logo_game=game))
        return True


