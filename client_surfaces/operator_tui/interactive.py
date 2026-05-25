from __future__ import annotations

import json
import os
import re
import shutil
import time
from concurrent.futures import Future, ThreadPoolExecutor
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncio
import math
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.color_depth import ColorDepth

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.plugins import PluginRegistry, default_plugin_registry
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import SECTIONS, get_section

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    "TUI: Focus [Tab], Command [:], Snake [Ctrl+S], Hilfe [?].",
    "Snake: B frame-mode, X Rahmen, C copy, V replace (nur command line).",
    "Architektur: Hub orchestriert, Worker fuehren aus; keine worker-zu-worker orchestration.",
    "Taskfluss: User -> Hub -> Task Queue -> Worker; Hub bleibt Control Plane.",
    "Betrieb: Hub/Worker getrennte Container, reproduzierbare Umgebungen.",
    "API evolution: additive, rueckwaertskompatibel, keine Big-Bang Refactors.",
)


class InteractiveOperatorTui:
    def __init__(
        self,
        state: OperatorState,
        registry: SectionAdapterRegistry | None = None,
        splash: SplashMachine | None = None,
    ) -> None:
        self._registry = registry or SectionAdapterRegistry()
        self._splash = splash
        self._plugins: PluginRegistry = default_plugin_registry()
        self.state = load_active_section(state, self._registry)
        self._tutorial_codecompass_cache: tuple[float, list[str]] = (0.0, [])
        self._tutorial_rag_cache: tuple[float, list[str]] = (0.0, [])
        self._tutorial_worker_cache: tuple[float, str] = (0.0, "")
        self._tutorial_worker_target_hint: str = ""
        self._tutorial_llm_cache: tuple[float, str] = (0.0, "")
        self._tutorial_llm_profile_cache: dict[str, Any] | None = None
        self._tutorial_llm_profile_key: str = ""
        self._tutorial_last_tip_text: str = ""
        self._tutorial_async_tip_future: Future[dict[str, str] | None] | None = None
        self._tutorial_async_tip_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-tutorial-ai")
        self._tutorial_async_next_refresh_at: float = 0.0
        self._tutorial_status_snapshot: dict[str, str] = {}
        self._tutorial_last_source: str = "local-knowledge"
        self._tutorial_last_target: str = "follow"
        self._command_buffer = ""
        self._rendered_text = self._render()
        self._control = FormattedTextControl(text=lambda: ANSI(self._rendered_text))
        self._output = Window(content=self._control, wrap_lines=False)
        self._app = Application(
            layout=Layout(self._output),
            key_bindings=self._build_keybindings(),
            full_screen=True,
            mouse_support=False,
            color_depth=ColorDepth.TRUE_COLOR,
        )

    def run(self) -> int:
        self._app.run(pre_run=self._on_app_start)
        return 0

    def _on_app_start(self) -> None:
        if self._splash is not None:
            self._app.create_background_task(self._splash_loop())
        if self._header_3d_active():
            self._app.create_background_task(self._header_logo_loop())

    def _header_3d_active(self) -> bool:
        enabled = os.environ.get("ANANTA_TUI_HEADER_3D", "1").strip().lower() not in {"0", "false", "no", "off"}
        no_3d = (self.state.terminal_graphics or {}).get("no_3d", False)
        return enabled and not no_3d

    async def _header_logo_loop(self) -> None:
        fps = max(1, min(60, int(os.environ.get("ANANTA_TUI_HEADER_3D_FPS", "24"))))
        delay = 1.0 / fps
        while True:
            self._tick_header_snake()
            self._rendered_text = self._render()
            self._app.invalidate()
            await asyncio.sleep(delay)

    async def _splash_loop(self) -> None:
        while self._splash is not None:
            ctx = self._splash.context
            from agent.cli.splash import SplashState
            if ctx.state in (SplashState.DISABLED, SplashState.SKIPPED, SplashState.COMPACT_HEADER):
                break
            self._splash.tick()
            self._rendered_text = self._render()
            self._app.invalidate()
            await asyncio.sleep(0.1)

    def _build_keybindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("q")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append("q")
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("q")
                return
            event.app.exit()

        @bindings.add(":")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append(":")
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(":")
                return
            self._command_buffer = ""
            self._set_state(self.state.with_updates(mode=OperatorMode.COMMAND, command_line=""))

        @bindings.add("enter")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_commit_message()
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._run_command(self._command_buffer)
                return
            if self.state.focus is FocusPane.HEADER:
                from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, cycle_value
                if 0 <= self.state.selected_index < len(CONFIG_ITEMS):
                    self._set_state(cycle_value(self.state, CONFIG_ITEMS[self.state.selected_index]))
                return
            if self.state.focus is FocusPane.NAVIGATION:
                if 0 <= self.state.selected_index < len(SECTIONS):
                    section = SECTIONS[self.state.selected_index]
                    self._run_command(f":section {section.id}")
                    self._set_state(self.state.with_updates(focus=FocusPane.CONTENT, selected_index=0))
                return
            self._run_command(":inspect")

        @bindings.add("escape")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_cancel_message()
                return
            if self._snake_mode_active():
                return
            self._command_buffer = ""
            self._run_command(":cancel")

        @bindings.add("backspace")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_backspace()
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_buffer = self._command_buffer[:-1]
                self._set_state(self.state.with_updates(command_line=self._command_buffer))

        @bindings.add("j")
        def _(event) -> None:
            def _j():
                self._set_state(self.state.with_updates(selected_index=self._clamp_down()))
            self._normal_or_text("j", _j)

        @bindings.add("k")
        def _(event) -> None:
            self._normal_or_text("k", lambda: self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1))))

        @bindings.add("e")
        def _(event) -> None:
            def _e():
                section = get_section(self.state.section_id)
                payload = (self.state.section_payloads or {}).get(section.id, {})
                plugin = self._plugins.launcher_for(payload, self.state.selected_index)
                if plugin is None:
                    return
                async def _run():
                    await event.app.run_in_terminal(
                        lambda: plugin.launch(payload, self.state.selected_index)
                    )
                event.app.create_background_task(_run())
            self._normal_or_text("e", _e)

        @bindings.add("h")
        def _(event) -> None:
            self._normal_or_text("h", lambda: self._move_focus(-1))

        @bindings.add("l")
        def _(event) -> None:
            self._normal_or_text("l", lambda: self._move_focus(1))

        @bindings.add("r")
        def _(event) -> None:
            self._normal_or_text("r", lambda: self._run_command(":refresh"))

        @bindings.add("?")
        def _(event) -> None:
            self._normal_or_text("?", lambda: self._run_command(":help"))

        @bindings.add("tab")
        def _(event) -> None:
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(" ")
                return
            self._move_focus(1)

        @bindings.add("space")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(" ")
                return
            if not self._snake_mode_active():
                return
            self._snake_immediate_brake()

        @bindings.add("c-s")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_snake_mode()

        @bindings.add("m")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("m")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("m")
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_message_mode()

        @bindings.add("x")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("x")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("x")
                return
            if not self._snake_mode_active():
                return
            self._snake_toggle_selection()

        @bindings.add("b")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("b")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("b")
                return
            if not self._snake_mode_active():
                return
            self._snake_toggle_frame_mode()

        @bindings.add("c")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("c")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("c")
                return
            if not self._snake_mode_active():
                return
            self._snake_copy_selection()

        @bindings.add("v")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("v")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("v")
                return
            if not self._snake_mode_active():
                return
            self._snake_replace_selection()

        @bindings.add("t")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("t")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("t")
                return
            if not self._snake_mode_active():
                return
            self._snake_cycle_message_style()

        @bindings.add("y")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("y")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("y")
                return
            if not self._snake_mode_active():
                return
            self._snake_cycle_color()

        @bindings.add("z")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("z")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("z")
                return
            if not self._snake_mode_active():
                return
            self._snake_clear_visual_marks()

        @bindings.add("u")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("u")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("u")
                return
            if not self._snake_mode_active():
                return
            self._toggle_tutorial_ai_mode()

        @bindings.add("left")
        def _(event) -> None:
            if self._try_header_snake_direction((-1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("right")
        def _(event) -> None:
            if self._try_header_snake_direction((1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add("up")
        def _(event) -> None:
            if self._try_header_snake_direction((0, -1)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("down")
        def _(event) -> None:
            if self._try_header_snake_direction((0, 1)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add("n")
        def _(event) -> None:
            self._normal_or_text("n", lambda: self._run_command(":next"))

        @bindings.add("p")
        def _(event) -> None:
            self._normal_or_text("p", lambda: self._run_command(":prev"))

        @bindings.add("g")
        def _(event) -> None:
            self._normal_or_text("g", lambda: self._set_state(self.state.with_updates(selected_index=0)))

        @bindings.add("G")
        def _(event) -> None:
            self._normal_or_text("G", lambda: self._set_state(self.state.with_updates(selected_index=999999)))

        @bindings.add("<any>")
        def _(event) -> None:
            if self._snake_message_mode_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._snake_message_append(data)
                return
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._append_command(data)

        return bindings

    def _normal_or_text(self, text: str, normal_action) -> None:
        if self._snake_message_mode_active():
            self._snake_message_append(text)
            return
        if self.state.mode is OperatorMode.COMMAND:
            self._append_command(text)
            return
        if self._snake_mode_active():
            return
        normal_action()

    def _append_command(self, text: str) -> None:
        self._command_buffer += text
        self._set_state(self.state.with_updates(command_line=self._command_buffer))

    def _run_command(self, command: str) -> None:
        result = execute_command(command, self.state)
        state = result.state.with_updates(status_message=result.message)
        if state.section_id != self.state.section_id or command.strip().lower() in {":refresh", "refresh", "r", ":next", ":prev"}:
            state = load_active_section(state, self._registry)
        self._command_buffer = ""
        self._set_state(state.with_updates(command_line=""))

    def _clamp_down(self) -> int:
        cur = self.state.selected_index
        if self.state.focus is FocusPane.NAVIGATION:
            return min(cur + 1, len(SECTIONS) - 1)
        if self.state.focus is FocusPane.HEADER:
            from client_surfaces.operator_tui.header_config import CONFIG_ITEMS
            return min(cur + 1, len(CONFIG_ITEMS) - 1)
        return cur + 1

    def _move_focus(self, delta: int) -> None:
        panes = (FocusPane.HEADER, FocusPane.NAVIGATION, FocusPane.CONTENT, FocusPane.DETAIL)
        cur = panes.index(self.state.focus)
        new_focus = panes[(cur + delta) % len(panes)]
        if new_focus is FocusPane.NAVIGATION:
            section_ids = [s.id for s in SECTIONS]
            try:
                new_selected = section_ids.index(self.state.section_id)
            except ValueError:
                new_selected = 0
        elif new_focus is FocusPane.HEADER or self.state.focus in (FocusPane.NAVIGATION, FocusPane.HEADER):
            new_selected = 0
        else:
            new_selected = self.state.selected_index
        next_state = self.state.with_updates(focus=new_focus, selected_index=new_selected)
        self._set_state(next_state)

    def _header_snake_enabled(self) -> bool:
        return os.environ.get("ANANTA_TUI_HEADER_SNAKE", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _default_header_snake(self) -> dict[str, object]:
        board_w, board_h = 18, 6
        snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        gaps = self._compute_snake_escape_gaps(board_w, board_h, seed=int(time.time() * 1000))
        return {
            "active": False,
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
            "message_style": "trail",
            "snake_color": "mint",
            "trail_window": max(1, min(120, int(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_WINDOW", "10")))),
            "trail_speed": max(0.2, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_SPEED", "8.0")))),
            "tutorial_mode": os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "0").strip().lower() in {"1", "true", "yes", "on"},
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

    def _tick_header_snake(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._header_snake_enabled():
            return
        if self.state.focus is not FocusPane.HEADER and not game.get("ui_steering"):
            return
        if not game or not game.get("active", False) or not game.get("alive", True):
            return
        tps = max(2, min(60, int(os.environ.get("ANANTA_TUI_HEADER_SNAKE_TPS", "18"))))
        step = 1.0 / tps
        now = time.monotonic()
        last_move = float(game.get("last_move", now))
        if (now - last_move) < step:
            return
        dt = max(step, now - last_move)

        free_mode = bool(game.get("free_mode"))
        if free_mode:
            size = shutil.get_terminal_size((120, 32))
            board_w = max(24, int(size.columns))
            board_h = max(12, int(size.lines - 1))
        else:
            board_w = max(18, int(game.get("board_w", 18)))
            board_h = max(6, int(game.get("board_h", 6)))
        game["board_w"] = board_w
        game["board_h"] = board_h
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
        game["moves"] = int(game.get("moves", 0)) + max(1, moved)
        game["last_move"] = now
        game["free_mode"] = free_mode
        self._update_multi_snake_state(game, now=now, board_w=board_w, board_h=board_h)
        mode_label = "fullscreen" if free_mode else "framed"
        next_state = self.state.with_updates(
            header_logo_game=game,
            status_message=f"snake:{mode_label} vx={vx:.1f} vy={vy:.1f}",
        )
        self.state = self._apply_snake_hover_selection_delay(next_state, head=snake[0], now=now)

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
        }
        snakes[local_id] = local_snapshot
        self._update_demo_remote_snakes(snakes, now=now, board_w=board_w, board_h=board_h)
        self._update_tutorial_ai_snake(game, snakes, now=now, board_w=board_w, board_h=board_h, enabled=bool(game.get("tutorial_mode")))
        game["snakes"] = snakes
        game["local_snake_id"] = local_id

    def _update_tutorial_ai_snake(
        self,
        game: dict[str, object],
        snakes: dict[str, dict[str, object]],
        *,
        now: float,
        board_w: int,
        board_h: int,
        enabled: bool,
    ) -> None:
        sid = "s-ai"
        if not enabled:
            snakes.pop(sid, None)
            return
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
        context_tokens = [*hints[:10], *rag_context[:10]]
        if self._tutorial_worker_target_hint:
            context_tokens.insert(0, f"target:{self._tutorial_worker_target_hint}")
        local = snakes.get(str(self.state.header_logo_game.get("local_snake_id", "s1"))) if isinstance(self.state.header_logo_game, dict) else None
        local_head = None
        if isinstance(local, dict):
            local_snake = local.get("snake")
            if isinstance(local_snake, list) and local_snake:
                head = local_snake[0]
                if isinstance(head, (list, tuple)) and len(head) == 2:
                    local_head = (int(head[0]) % max(1, board_w), int(head[1]) % max(1, board_h))
        target = self._tutorial_ai_target_cell(
            board_w=board_w,
            board_h=board_h,
            context_tokens=context_tokens,
            local_head=local_head,
        )
        existing = snakes.get(sid, {})
        existing_snake_raw = existing.get("snake") if isinstance(existing, dict) else []
        existing_snake = [
            (int(p[0]) % max(1, board_w), int(p[1]) % max(1, board_h))
            for p in (existing_snake_raw or [])
            if isinstance(p, (list, tuple)) and len(p) == 2
        ]
        if existing_snake:
            start_head = existing_snake[0]
        else:
            start_head = ((target[0] - 1) % max(1, board_w), target[1] % max(1, board_h))
            existing_snake = [start_head]
        new_head = self._step_toward_cell(
            current=start_head,
            target=target,
            board_w=board_w,
            board_h=board_h,
        )
        body = [new_head, *existing_snake]
        while len(body) < 10:
            tx = (body[-1][0] - 1) % max(1, board_w)
            body.append((tx, body[-1][1]))
        body = body[:10]
        trail = list(body)
        tip = self._tutorial_ai_tip(now=now)
        target_label = self._tutorial_last_target or self._tutorial_target_label(board_w=board_w, board_h=board_h, target=target)
        source_label = self._tutorial_last_source or "codecompass-rag"
        self._record_tutorial_propose_event(
            game,
            now=now,
            source=source_label,
            target=target_label,
            text=tip,
        )
        snakes[sid] = {
            "id": sid,
            "pseudonym": "tutor-ai",
            "oidc_provider": "codecompass-ai",
            "snake": body,
            "trail_path": trail,
            "selection_cells": [],
            "message": tip,
            "message_style": "ticker",
            "snake_color": "amber",
            "trail_window": 28,
            "trail_speed": 8.0,
            "active": True,
            "updated_at": now,
            "local": False,
            "knowledge_scope": ("tui", "architecture", "workflow"),
            "target_cell": target,
        }

    def _tutorial_target_label(
        self,
        *,
        board_w: int,
        board_h: int,
        target: tuple[int, int],
    ) -> str:
        tx, ty = int(target[0]), int(target[1])
        if ty <= max(2, board_h // 6):
            return "header"
        if tx <= max(2, board_w // 4):
            return "nav"
        if tx >= max(2, board_w - max(8, board_w // 4)) and ty >= max(2, board_h - max(6, board_h // 4)):
            return "detail"
        return "content"

    def _record_tutorial_propose_event(
        self,
        game: dict[str, object],
        *,
        now: float,
        source: str,
        target: str,
        text: str,
    ) -> None:
        history_raw = game.get("tutorial_propose_history")
        history: list[dict[str, object]]
        if isinstance(history_raw, list):
            history = [dict(entry) for entry in history_raw if isinstance(entry, dict)]
        else:
            history = []
        entry = {
            "at": float(now),
            "source": str(source or "unknown"),
            "target": str(target or "content"),
            "text": str(text or "").strip(),
        }
        if not entry["text"]:
            return
        last = history[-1] if history else None
        if isinstance(last, dict):
            if (
                str(last.get("source") or "") == str(entry["source"])
                and str(last.get("target") or "") == str(entry["target"])
                and str(last.get("text") or "") == str(entry["text"])
            ):
                return
        history.append(entry)
        game["tutorial_propose_history"] = history[-8:]

    def _tutorial_ai_target_cell(
        self,
        *,
        board_w: int,
        board_h: int,
        context_tokens: list[str],
        local_head: tuple[int, int] | None,
    ) -> tuple[int, int]:
        text = " ".join(context_tokens).lower()
        if "target:header" in text:
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if "target:nav" in text:
            return (max(1, board_w // 5), max(2, board_h // 2))
        if "target:content" in text:
            return (max(2, board_w // 2), max(2, board_h // 2))
        if "target:detail" in text:
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if "target:follow" in text and local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        if any(token in text for token in ("endpoint", "auth", "header", "config", "oidc")):
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if any(token in text for token in ("task", "goal", "section", "navigation", "queue")):
            return (max(1, board_w // 5), max(2, board_h // 2))
        if any(token in text for token in ("detail", "inspect", "artifact", "context", "result")):
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        return (max(2, board_w // 2), max(2, board_h // 2))

    def _step_toward_cell(
        self,
        *,
        current: tuple[int, int],
        target: tuple[int, int],
        board_w: int,
        board_h: int,
    ) -> tuple[int, int]:
        cx, cy = int(current[0]), int(current[1])
        tx, ty = int(target[0]), int(target[1])
        dx = tx - cx
        dy = ty - cy
        if abs(dx) >= abs(dy) and dx != 0:
            step_x = 1 if dx > 0 else -1
            return ((cx + step_x) % max(1, board_w), cy % max(1, board_h))
        if dy != 0:
            step_y = 1 if dy > 0 else -1
            return (cx % max(1, board_w), (cy + step_y) % max(1, board_h))
        return (cx % max(1, board_w), cy % max(1, board_h))

    def _tutorial_ai_tip(self, *, now: float) -> str:
        status = self._tutorial_status_delta_summary()
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
        if not self._tutorial_async_enabled():
            result = self._tutorial_ai_tip_sync(now=now, status=status, hints=hints, rag_context=rag_context)
            if result:
                self._tutorial_last_source = result.get("source", self._tutorial_last_source)
                self._tutorial_last_target = result.get("target", self._tutorial_last_target)
                self._tutorial_last_tip_text = result.get("text", self._tutorial_last_tip_text)
            return self._tutorial_last_tip_text

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        self._poll_tutorial_async_tip_result()
        if self._tutorial_async_tip_future is None and now >= self._tutorial_async_next_refresh_at:
            self._tutorial_async_next_refresh_at = now + refresh_seconds
            self._tutorial_async_tip_future = self._tutorial_async_tip_executor.submit(
                self._tutorial_ai_tip_sync,
                now=now,
                status=status,
                hints=list(hints),
                rag_context=list(rag_context),
            )
        if self._tutorial_last_tip_text:
            return self._tutorial_last_tip_text
        return "KI-Schlange analysiert UI-Delta…"

    def _tutorial_async_enabled(self) -> bool:
        enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_ASYNC", "1")).strip().lower() in {"1", "true", "yes", "on"}
        return bool(enabled and getattr(self._app, "is_running", False))

    def _poll_tutorial_async_tip_result(self) -> None:
        future = self._tutorial_async_tip_future
        if future is None or not future.done():
            return
        result = future.result()
        self._tutorial_async_tip_future = None
        if not isinstance(result, dict):
            return
        text = str(result.get("text") or "").strip()
        if not text:
            return
        self._tutorial_last_tip_text = text
        self._tutorial_last_source = str(result.get("source") or self._tutorial_last_source)
        self._tutorial_last_target = str(result.get("target") or self._tutorial_last_target)

    def _tutorial_status_delta_summary(self) -> str:
        mode = self.state.mode.value
        focus = self.state.focus.value
        section = self.state.section_id
        selected = self.state.selected_index
        snapshot = {
            "mode": str(mode),
            "focus": str(focus),
            "section": str(section),
            "idx": str(selected),
            "state": str((self.state.panel_states or {}).get(section, "")),
        }
        previous = dict(self._tutorial_status_snapshot)
        changed = [f"{key}={value}" for key, value in snapshot.items() if previous.get(key) != value]
        self._tutorial_status_snapshot = snapshot
        if not previous:
            return f"TUI state mode={mode} focus={focus} section={section} idx={selected}."
        if not changed:
            return "TUI delta: unchanged."
        return "TUI delta: " + ", ".join(changed)

    def _tutorial_ai_tip_sync(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> dict[str, str]:
        worker_tip = self._tutorial_ai_worker_propose_message(now=now, status=status, hints=hints, rag_context=rag_context)
        if worker_tip:
            return {
                "source": "worker-propose",
                "target": self._tutorial_worker_target_hint or "follow",
                "text": worker_tip,
            }
        llm_hints = [*hints[:12], *[f"RAG {entry}" for entry in rag_context[:8]]]
        llm_tip = self._tutorial_ai_llm_message(now=now, status=status, hints=llm_hints)
        if llm_tip:
            return {
                "source": "openai-compatible",
                "target": self._tutorial_worker_target_hint or "content",
                "text": llm_tip,
            }
        if not hints and not rag_context:
            base = _TUTORIAL_AI_KNOWLEDGE[int(now * 0.5) % len(_TUTORIAL_AI_KNOWLEDGE)]
            return {
                "source": "local-knowledge",
                "target": "follow",
                "text": f"{status} {base}",
            }
        cc = hints[int(now * 0.7) % len(hints)] if hints else ""
        rag = rag_context[int(now * 0.9) % len(rag_context)] if rag_context else ""
        parts = [status]
        if cc:
            parts.append(f"CodeCompass: {cc}")
        if rag:
            parts.append(f"RAG: {rag}")
        return {
            "source": "codecompass-rag",
            "target": "content",
            "text": " ".join(parts),
        }

    def _tutorial_ai_worker_propose_message(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> str | None:
        backend = str(os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND", "")).strip().lower()
        if backend not in {"worker-propose", "worker", "opencode", "hermes"}:
            return None
        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_worker_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "worker-propose"
            if self._tutorial_worker_target_hint:
                self._tutorial_last_target = self._tutorial_worker_target_hint
            return cached_msg

        base_url = str(self.state.endpoint or os.environ.get("ANANTA_BASE_URL") or "http://localhost:5000").strip()
        if not base_url:
            return None
        timeout_seconds = max(0.3, min(12.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "")).strip()
        provider = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_PROVIDER", "")).strip()
        if not provider and backend in {"opencode", "hermes"}:
            provider = backend

        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints"
        rag_block = "\n".join(f"- {h}" for h in rag_context[:8]) if rag_context else "- no rag_helper context"
        prompt = (
            f"{status}\n"
            "You are the tutorial snake controller for Ananta TUI.\n"
            "Use CodeCompass and rag_helper context.\n"
            "Return exactly one line <=180 chars with immediate guidance.\n"
            "Prefix the line with one steering tag in this format: [target=header|nav|content|detail|follow].\n"
            f"CodeCompass hints:\n{hint_block}\n"
            f"rag_helper context:\n{rag_block}\n"
        )
        payload: dict[str, object] = {"prompt": prompt, "temperature": 0.2}
        if model:
            payload["model"] = model
        if provider:
            payload["provider"] = provider
        strategy_mode = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_STRATEGY", "")).strip()
        if strategy_mode:
            payload["strategy_mode"] = strategy_mode
        token = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_TOKEN", "")).strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url=base_url.rstrip("/") + "/step/propose",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
        if not isinstance(data, dict):
            return None
        text = str(data.get("reason") or data.get("raw") or "").strip()
        if not text:
            return None
        single_line = " ".join(text.split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "worker-propose"
        self._tutorial_last_target = target_hint or "follow"
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        self._tutorial_worker_cache = (now, clipped)
        return clipped

    def _tutorial_ai_llm_message(self, *, now: float, status: str, hints: list[str]) -> str | None:
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL") or "google/gemma-4-e4b").strip()
        api_base = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or "http://192.168.178.100:1234"
        ).strip()
        api_token = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        if not (model and api_base):
            return None
        parsed_api_base = urlparse(api_base)
        if (not parsed_api_base.path or parsed_api_base.path == "/") and parsed_api_base.netloc.endswith(":1234"):
            api_base = api_base.rstrip("/") + "/v1"

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_llm_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "openai-compatible"
            self._tutorial_last_target = "content"
            return cached_msg

        timeout_seconds = max(0.3, min(10.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        profile = self._resolve_tutorial_llm_profile(
            now=now,
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints available"
        prompt = (
            f"{status}\n"
            f"{str(profile.get('user_prompt') or '')}\n"
            f"CodeCompass + rag_helper hints:\n{hint_block}\n"
            "Max 180 chars."
        )
        content = self._tutorial_llm_chat_completion(
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            system_prompt=str(profile.get("system_prompt") or "You are a concise in-product tutorial assistant."),
            user_prompt=prompt,
            temperature=float(profile.get("temperature") or 0.15),
            max_tokens=int(profile.get("max_tokens") or 72),
        )
        if not content:
            return None
        parsed = self._parse_tutorial_ai_llm_content(content)
        if not parsed:
            return None
        clipped, target_hint = parsed
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "openai-compatible"
        self._tutorial_last_target = target_hint or "content"
        self._tutorial_llm_cache = (now, clipped)
        return clipped

    def _resolve_tutorial_llm_profile(
        self,
        *,
        now: float,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        profile_key = f"{model}@{api_base}"
        if self._tutorial_llm_profile_cache and self._tutorial_llm_profile_key == profile_key:
            return dict(self._tutorial_llm_profile_cache)

        default_profile: dict[str, Any] = {
            "id": "compact-plain",
            "system_prompt": "You are a concise in-product tutorial assistant.",
            "user_prompt": "Provide one concise tutorial line for a snake assistant in this TUI. Focus on the immediate next action.",
            "temperature": 0.15,
            "max_tokens": 72,
        }
        training_enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_TRAINING", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if not training_enabled:
            self._tutorial_llm_profile_key = profile_key
            self._tutorial_llm_profile_cache = dict(default_profile)
            return dict(default_profile)

        candidates: list[dict[str, Any]] = [
            dict(default_profile),
            {
                "id": "compact-tagged",
                "system_prompt": "You are a concise in-product tutorial assistant.",
                "user_prompt": (
                    "Return exactly one short line with one steering prefix "
                    "[target=header|nav|content|detail|follow] and immediate next action."
                ),
                "temperature": 0.1,
                "max_tokens": 64,
            },
        ]

        best_profile: dict[str, Any] = dict(default_profile)
        best_score: tuple[int, float] = (-1, 999.0)
        for candidate in candidates:
            probe_prompt = (
                "TUI mode=normal focus=content section=dashboard idx=0.\n"
                f"{str(candidate.get('user_prompt') or '')}\n"
                "CodeCompass + rag_helper hints:\n- queue depth\n- tasks pending\n"
                "Max 180 chars."
            )
            started = time.monotonic()
            content = self._tutorial_llm_chat_completion(
                model=model,
                api_base=api_base,
                api_token=api_token,
                timeout_seconds=min(1.8, timeout_seconds),
                system_prompt=str(candidate.get("system_prompt") or ""),
                user_prompt=probe_prompt,
                temperature=float(candidate.get("temperature") or 0.15),
                max_tokens=int(candidate.get("max_tokens") or 72),
            )
            elapsed = time.monotonic() - started
            parsed = self._parse_tutorial_ai_llm_content(content or "")
            if not parsed:
                continue
            _, target_hint = parsed
            structure_bonus = 1 if target_hint else 0
            score = (1 + structure_bonus, elapsed)
            if score[0] > best_score[0] or (score[0] == best_score[0] and score[1] < best_score[1]):
                best_score = score
                best_profile = dict(candidate)

        self._tutorial_llm_profile_key = profile_key
        self._tutorial_llm_profile_cache = dict(best_profile)
        return dict(best_profile)

    def _tutorial_llm_chat_completion(
        self,
        *,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": max(24, min(120, int(max_tokens))),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        request = urllib.request.Request(
            url=api_base.rstrip("/") + "/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = str(message.get("content") or "").strip()
        return content or None

    def _parse_tutorial_ai_llm_content(self, content: str) -> tuple[str, str] | None:
        single_line = " ".join(str(content or "").split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        elif single_line.startswith("{") and single_line.endswith("}"):
            try:
                payload = json.loads(single_line)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                text = str(payload.get("text") or payload.get("message") or "").strip()
                target = str(payload.get("target") or "").strip().lower()
                if target in {"header", "nav", "content", "detail", "follow"}:
                    target_hint = target
                if text:
                    single_line = " ".join(text.split())
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        return clipped, target_hint

    def _load_codecompass_hints(self, *, now: float) -> list[str]:
        cached_at, cached = self._tutorial_codecompass_cache
        if cached and (now - cached_at) < 6.0:
            return cached

        out_dir = self._resolve_codecompass_output_dir()
        if out_dir is None:
            self._tutorial_codecompass_cache = (now, [])
            return []

        try:
            from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader
        except Exception:
            self._tutorial_codecompass_cache = (now, [])
            return []

        try:
            payload = CodeCompassOutputReader().load_from_output_dir(output_dir=out_dir)
            records = payload.get("records") if isinstance(payload, dict) else []
            if not isinstance(records, list):
                records = []
            hints: list[str] = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                kind = str(record.get("kind") or record.get("type") or "").strip()
                file_path = str(record.get("file") or record.get("path") or "").strip()
                name = str(record.get("name") or record.get("id") or "").strip()
                if not (kind or file_path or name):
                    continue
                parts = []
                if kind:
                    parts.append(kind)
                if name:
                    parts.append(name)
                if file_path:
                    parts.append(file_path)
                hint = " · ".join(parts)
                if hint:
                    hints.append(hint)
                if len(hints) >= 64:
                    break
            self._tutorial_codecompass_cache = (now, hints)
            return hints
        except Exception:
            self._tutorial_codecompass_cache = (now, [])
            return []

    def _load_rag_helper_context(self, *, now: float) -> list[str]:
        cached_at, cached = self._tutorial_rag_cache
        if cached and (now - cached_at) < 6.0:
            return cached

        out_dir = self._resolve_codecompass_output_dir()
        if out_dir is None:
            self._tutorial_rag_cache = (now, [])
            return []

        manifest_path = out_dir / "manifest.json"
        files: list[str] = []
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            partitioned = manifest.get("partitioned_outputs") if isinstance(manifest, dict) else None
            if isinstance(partitioned, dict):
                for values in partitioned.values():
                    if not isinstance(values, list):
                        continue
                    for item in values:
                        rel = str(item or "").strip()
                        if rel:
                            files.append(rel)
        files.extend(["context.jsonl", "details.jsonl", "index.jsonl", "xml_overview.jsonl"])
        deduped_files: list[str] = []
        seen_files: set[str] = set()
        for rel in files:
            normalized = rel.strip().lstrip("/")
            if not normalized or normalized in seen_files:
                continue
            seen_files.add(normalized)
            deduped_files.append(normalized)

        context: list[str] = []
        for rel in deduped_files[:24]:
            path = out_dir / rel
            if not path.exists() or not path.is_file() or path.suffix.lower() != ".jsonl":
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                payload = line.strip()
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                tokens = [
                    str(parsed.get("domain") or "").strip(),
                    str(parsed.get("kind") or "").strip(),
                    str(parsed.get("title") or "").strip(),
                    str(parsed.get("section_title") or "").strip(),
                    str(parsed.get("name") or "").strip(),
                    str(parsed.get("file") or parsed.get("path") or "").strip(),
                    str(parsed.get("summary") or "").strip(),
                    str(parsed.get("content") or parsed.get("text") or "").strip(),
                ]
                text = " · ".join(part for part in tokens if part)
                compact = " ".join(text.split())
                if not compact:
                    continue
                context.append(compact[:220])
                if len(context) >= 64:
                    break
            if len(context) >= 64:
                break
        self._tutorial_rag_cache = (now, context)
        return context

    def _resolve_codecompass_output_dir(self) -> Path | None:
        candidates = [
            os.environ.get("ANANTA_TUI_CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("ANANTA_CODECOMPASS_OUTPUT_DIR"),
            "rag-helper/out",
            "rag-helper/output",
            "codecompass-out",
        ]
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if path.exists() and path.is_dir() and (path / "index.jsonl").exists():
                return path
        return None

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
            phase = now * (0.9 + i * 0.3)
            hx = int(center_x + radius_x * math.sin(phase + i * 1.7)) % max(1, board_w)
            hy = int(center_y + radius_y * math.cos(phase + i * 1.3)) % max(1, board_h)
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
            game["active"] = False
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
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: an"))

    def _toggle_tutorial_ai_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("tutorial_mode"))
        game["tutorial_mode"] = not enabled
        label = "an" if not enabled else "aus"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake tutorial-ai: {label}"))

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
        game = dict(self.state.header_logo_game or {})
        if game.get("free_mode"):
            game["free_mode"] = False
        temp_state = self.state.with_updates(header_logo_game=game)
        size = shutil.get_terminal_size((120, 32))
        rendered = render_operator_shell(temp_state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
        return [_ANSI_STRIP.sub("", line) for line in rendered.splitlines()]

    def _snake_copy_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        cells_raw = game.get("selection_cells") or []
        cells = [
            (int(c[0]), int(c[1]))
            for c in cells_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake copy: keine auswahl"))
            return
        lines = self._snake_render_plain_lines()
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
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="snake copy: in clipboard + message",
            )
        )

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
        game["message"] = message
        game["message_mode"] = False
        game["message_draft"] = ""
        self._save_snake_message_config(message)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: gespeichert"))

    def _save_snake_message_config(self, message: str) -> None:
        cfg_dir = Path.home() / ".config" / "ananta"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "snake-config.json"
        payload = {"snake_message": message, "updated_at": int(time.time())}
        cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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

    def _set_state(self, state: OperatorState) -> None:
        if self._splash is not None:
            from agent.cli.splash import SplashState
            ctx = self._splash.context
            if ctx.state in (SplashState.FULLSCREEN, SplashState.TRANSITION):
                self._splash.transition_to(SplashState.COMPACT_HEADER)
        self.state = state
        self._rendered_text = self._render()
        self._app.invalidate()

    def _render(self) -> str:
        if self._splash is not None:
            self._splash.tick()
        size = shutil.get_terminal_size((120, 32))
        return render_operator_shell(self.state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
