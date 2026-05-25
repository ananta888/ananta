from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import asyncio
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
            "board_w": board_w,
            "board_h": board_h,
            "snake": snake,
            "trail_path": list(snake),
            "mark_cells": [],
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
        mode_label = "fullscreen" if free_mode else "framed"
        next_state = self.state.with_updates(
            header_logo_game=game,
            status_message=f"snake:{mode_label} vx={vx:.1f} vy={vy:.1f}",
        )
        self.state = self._apply_snake_hover_selection_delay(next_state, head=snake[0], now=now)

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
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: aus"))
            return
        game["active"] = True
        game["ui_steering"] = True
        game["free_mode"] = True
        game["message_mode"] = False
        game["message_draft"] = ""
        game["last_move"] = time.monotonic()
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: an"))

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
