from __future__ import annotations

import os
import shutil
import time
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
        if self.state.focus is FocusPane.HEADER:
            self.state = self._activate_header_snake(self.state)
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
        fps = max(1, min(60, int(os.environ.get("ANANTA_TUI_HEADER_3D_FPS", "12"))))
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
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("q")
                return
            event.app.exit()

        @bindings.add(":")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(":")
                return
            self._command_buffer = ""
            self._set_state(self.state.with_updates(mode=OperatorMode.COMMAND, command_line=""))

        @bindings.add("enter")
        def _(event) -> None:
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
            self._command_buffer = ""
            self._run_command(":cancel")

        @bindings.add("backspace")
        def _(event) -> None:
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
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(" ")
                return
            self._move_focus(1)

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
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._append_command(data)

        return bindings

    def _normal_or_text(self, text: str, normal_action) -> None:
        if self.state.mode is OperatorMode.COMMAND:
            self._append_command(text)
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
        if new_focus is FocusPane.HEADER:
            next_state = self._activate_header_snake(next_state)
        elif self.state.focus is FocusPane.HEADER:
            next_state = self._deactivate_header_snake(next_state)
        self._set_state(next_state)

    def _header_snake_enabled(self) -> bool:
        return os.environ.get("ANANTA_TUI_HEADER_SNAKE", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _default_header_snake(self) -> dict[str, object]:
        board_w, board_h = 18, 6
        snake = [(4, 3), (3, 3), (2, 3)]
        return {
            "active": True,
            "alive": True,
            "board_w": board_w,
            "board_h": board_h,
            "snake": snake,
            "direction": (1, 0),
            "next_direction": (1, 0),
            "food": (12, 3),
            "score": 0,
            "moves": 0,
            "last_move": time.monotonic(),
        }

    def _activate_header_snake(self, state: OperatorState) -> OperatorState:
        if not self._header_snake_enabled():
            return state
        game = dict(state.header_logo_game or self._default_header_snake())
        game["active"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        game["last_move"] = time.monotonic()
        return state.with_updates(header_logo_game=game)

    def _deactivate_header_snake(self, state: OperatorState) -> OperatorState:
        game = dict(state.header_logo_game or {})
        if not game:
            return state
        game["active"] = False
        return state.with_updates(header_logo_game=game)

    def _try_header_snake_direction(self, direction: tuple[int, int]) -> bool:
        if self.state.mode is OperatorMode.COMMAND:
            return False
        if self.state.focus is not FocusPane.HEADER or not self._header_snake_enabled():
            return False
        game = dict(self.state.header_logo_game or {})
        if not game:
            game = self._default_header_snake()
        if not game.get("active", False):
            game["active"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        current = tuple(game.get("direction", (1, 0)))
        if direction[0] == -current[0] and direction[1] == -current[1]:
            return True
        game["next_direction"] = direction
        self._set_state(self.state.with_updates(header_logo_game=game))
        return True

    def _tick_header_snake(self) -> None:
        if self.state.focus is not FocusPane.HEADER or not self._header_snake_enabled():
            return
        game = dict(self.state.header_logo_game or {})
        if not game or not game.get("active", False) or not game.get("alive", True):
            return
        tps = max(3, min(20, int(os.environ.get("ANANTA_TUI_HEADER_SNAKE_TPS", "8"))))
        step = 1.0 / tps
        now = time.monotonic()
        last_move = float(game.get("last_move", now))
        if (now - last_move) < step:
            return

        board_w = max(6, int(game.get("board_w", 18)))
        board_h = max(4, int(game.get("board_h", 6)))
        snake_raw = game.get("snake") or []
        snake = [(int(p[0]), int(p[1])) for p in snake_raw if isinstance(p, (list, tuple)) and len(p) == 2]
        if not snake:
            snake = [(4, 3), (3, 3), (2, 3)]
        direction = tuple(game.get("direction", (1, 0)))
        next_direction = tuple(game.get("next_direction", direction))
        if next_direction[0] == -direction[0] and next_direction[1] == -direction[1]:
            next_direction = direction
        direction = next_direction

        hx, hy = snake[0]
        nx = (hx + direction[0]) % board_w
        ny = (hy + direction[1]) % board_h
        new_head = (nx, ny)

        food_raw = game.get("food", (12, 3))
        food = (int(food_raw[0]), int(food_raw[1])) if isinstance(food_raw, (list, tuple)) and len(food_raw) == 2 else (12, 3)
        grow = new_head == food
        body_to_check = snake if grow else snake[:-1]
        if new_head in body_to_check:
            game["alive"] = False
            game["active"] = True
            game["direction"] = direction
            game["next_direction"] = direction
            game["last_move"] = now
            self.state = self.state.with_updates(header_logo_game=game, status_message="snake: game over (tab zurück, dann erneut)")
            return

        snake = [new_head, *snake]
        if not grow:
            snake.pop()
        else:
            game["score"] = int(game.get("score", 0)) + 1
            game["food"] = self._spawn_snake_food(board_w, board_h, snake, int(game.get("moves", 0)) + 1)

        game["snake"] = snake
        game["direction"] = direction
        game["next_direction"] = direction
        game["moves"] = int(game.get("moves", 0)) + 1
        game["last_move"] = now
        self.state = self.state.with_updates(header_logo_game=game)

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
