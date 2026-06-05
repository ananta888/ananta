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
from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings

_TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT = (
    "You are tutorial-snake guidance.\n"
    "Priority: {priority}\n"
    "User feed: {user_feed}\n"
    "Contact zone: {contact_zone}\n"
    "Respond with one immediate actionable hint (max 180 chars)."
)


class HeaderSnakeMixin:
    """Mixin providing header snake enable/disable and direction handling."""

    _PERSISTED_TUI_KEYS = (
        "tutorial_mode",
        "chat_panel_open",
        "ai_snake_provider_preference",
        "ai_visual_use_codecompass",
        "chat_backend",
        "chat_backend_model",
        "chat_backend_api_base",
        "chat_ask_timeout_s",
        "chat_use_codecompass",
        "chat_include_local_project",
        "chat_include_wikipedia",
        "chat_source_pack_id",
        "chat_context_chars",
        "chat_max_tokens",
        "chat_rag_top_k",
        "chat_answer_chars",
        # Memory settings (CMW track)
        "chat_use_history",
        "chat_history_turns",
        "chat_history_chars",
        "chat_use_summary",
        "chat_summary_chars",
        "chat_summary_update_every_turns",
        "chat_pass_memory_to_worker",
        "chat_worker_mode",
        "chat_backend_fallback",
        "chat_include_runtime_status",
    )

    def _header_snake_enabled(self) -> bool:
        return os.environ.get("ANANTA_TUI_HEADER_SNAKE", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _default_header_snake(self) -> dict[str, object]:
        cfg = self._load_snake_message_config()
        # Load from legacy tui_chat_settings.json (CWD-scoped)
        persisted_cfg = load_tui_chat_settings()
        # Overlay with UserConfigManager (project user.json → ~/.anana/user.json)
        try:
            from client_surfaces.operator_tui.config.user_config_manager import (
                _DEFAULTS,
                _read_json,
                global_config_path,
                load_user_config,
                project_config_path,
            )
            user_cfg = load_user_config()
            explicit_user_keys = set(_read_json(global_config_path())) | set(_read_json(project_config_path()))
            # user_cfg merges defaults→global→project; ignore bare defaults when legacy
            # settings already carry an explicit value.
            for k, v in user_cfg.items():
                if isinstance(v, (str, int, float, bool)):
                    if k in explicit_user_keys or (k in persisted_cfg and v != _DEFAULTS.get(k)):
                        persisted_cfg[k] = v
        except Exception:
            pass
        board_w, board_h = 18, 6
        snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        gaps = self._compute_snake_escape_gaps(board_w, board_h, seed=int(time.time() * 1000))
        _snake_mode_on = os.environ.get("ANANTA_TUI_SNAKE_MODE", "1").strip().lower() not in {"0", "false", "no", "off"}
        _share_only_nav_e2e = os.environ.get("ANANTA_TUI_E2E_SHARE_ONLY_NAV", "0").strip().lower() in {"1", "true", "yes", "on"}
        _tutorial_on = os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "0").strip().lower() not in {"0", "false", "no", "off"}
        game: dict[str, object] = {
            "active": _snake_mode_on,
            "alive": True,
            "ui_steering": _snake_mode_on,
            # For share-only E2E captures, keep snake mode active but avoid the
            # fullscreen overlay so Share panel content remains visible.
            "free_mode": _snake_mode_on and not _share_only_nav_e2e,
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
            "ai_snake_mode": "off",
            "ai_snake_provider_preference": os.environ.get("ANANTA_TUI_AI_SNAKE_PROVIDER", "lmstudio"),
            "ai_snake_provider_model": os.environ.get("ANANTA_TUI_AI_SNAKE_MODEL", "ananta-smoke"),
            "ai_snake_provider_cloud_allowed": os.environ.get("ANANTA_TUI_AI_SNAKE_CLOUD_ALLOWED", "0").strip().lower() in {"1", "true", "yes", "on"},
            "ai_snake_provider_max_latency_ms": max(250, int(os.environ.get("ANANTA_TUI_AI_SNAKE_MAX_LATENCY_MS", "2000"))),
            "ai_snake_prediction": {},
            "ai_snake_debug": {},
            "ai_snake_runtime_status": "idle",
            "ai_training_context_released": False,
            "ai_snake_follow_state": make_follow_state(ai_position=(3, 3), mode="off"),
            "chat_panel_open": True,
            "chat_backend": os.environ.get("ANANTA_TUI_CHAT_BACKEND", "lmstudio"),
            "chat_backend_model": os.environ.get("ANANTA_TUI_CHAT_MODEL", os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "google/gemma-4-e4b")),
            "chat_backend_api_base": os.environ.get(
                "ANANTA_TUI_CHAT_API_BASE_URL",
                os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://192.168.178.100:1234/v1"),
            ),
            "chat_backends_available": [
                item.strip()
                for item in str(os.environ.get("ANANTA_TUI_CHAT_BACKENDS", "ananta-worker,opencode,lmstudio,hermes")).split(",")
                if item.strip()
            ],
            "chat_backend_models": [],
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
        for key in self._PERSISTED_TUI_KEYS:
            value = persisted_cfg.get(key)
            if isinstance(value, (str, int, float, bool)):
                game[key] = value
        env_overrides = {
            "chat_backend": os.environ.get("ANANTA_TUI_CHAT_BACKEND"),
            "chat_backend_model": os.environ.get("ANANTA_TUI_CHAT_MODEL"),
            "chat_backend_api_base": os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL"),
            "chat_ask_timeout_s": os.environ.get("ANANTA_TUI_CHAT_ASK_TIMEOUT"),
            "chat_context_chars": os.environ.get("ANANTA_TUI_CHAT_CONTEXT_CHARS"),
            "chat_max_tokens": os.environ.get("ANANTA_TUI_CHAT_MAX_TOKENS"),
            "chat_rag_top_k": os.environ.get("ANANTA_TUI_CHAT_RAG_TOP_K"),
            "chat_answer_chars": os.environ.get("ANANTA_TUI_CHAT_ANSWER_CHARS"),
            "chat_backend_fallback": os.environ.get("ANANTA_TUI_CHAT_BACKEND_FALLBACK"),
        }
        for key, value in env_overrides.items():
            if value is not None and str(value).strip():
                game[key] = value
        if bool(game.get("active")) and bool(game.get("ui_steering")) and bool(game.get("chat_panel_open", True)):
            try:
                from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
                chat = get_chat_state(game)
                switch_channel(chat, "ai:tutor", preserve_input=True)
                chat["chat_focus"] = True
                chat["chat_input_buffer"] = str(chat.get("chat_input_buffer") or "")
                chat["chat_input_cursor"] = len(str(chat.get("chat_input_buffer") or ""))
                chat["chat_input_history_index"] = None
                set_chat_state(game, chat)
            except Exception:
                pass
        # Inject persisted chat input history into game state
        try:
            if hasattr(self, "_apply_input_history_to_game"):
                self._apply_input_history_to_game(game)
        except Exception:
            pass
        return game

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
