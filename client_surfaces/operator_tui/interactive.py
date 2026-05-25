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
from client_surfaces.operator_tui.logo_inline import build_snake_control_boxes
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
        snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        gaps = self._compute_snake_escape_gaps(board_w, board_h, seed=int(time.time() * 1000))
        return {
            "active": True,
            "alive": True,
            "ui_steering": True,
            "board_w": board_w,
            "board_h": board_h,
            "snake": snake,
            "direction": (1, 0),
            "next_direction": (1, 0),
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
        steering = self.state.focus is FocusPane.HEADER or bool(game.get("ui_steering"))
        if not steering:
            return False
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
        game = dict(self.state.header_logo_game or {})
        if not self._header_snake_enabled():
            return
        if self.state.focus is not FocusPane.HEADER and not game.get("ui_steering"):
            return
        if not game or not game.get("active", False) or not game.get("alive", True):
            return
        tps = max(1, min(12, int(os.environ.get("ANANTA_TUI_HEADER_SNAKE_TPS", "4"))))
        step = 1.0 / tps
        now = time.monotonic()
        last_move = float(game.get("last_move", now))
        if (now - last_move) < step:
            return

        size = shutil.get_terminal_size((120, 32))
        board_w = max(24, int(size.columns))
        board_h = max(12, int(size.lines - 1))
        game["board_w"] = board_w
        game["board_h"] = board_h
        boxes = self._compute_control_boxes(board_w, board_h)
        game["boxes"] = boxes
        snake_raw = game.get("snake") or []
        snake = [(int(p[0]), int(p[1])) for p in snake_raw if isinstance(p, (list, tuple)) and len(p) == 2]
        if not snake:
            snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        snake = [((x % board_w), (y % board_h)) for x, y in snake]
        direction = tuple(game.get("direction", (1, 0)))
        next_direction = tuple(game.get("next_direction", direction))
        if next_direction[0] == -direction[0] and next_direction[1] == -direction[1]:
            next_direction = direction
        direction = next_direction

        hx, hy = snake[0]
        nx = (hx + direction[0]) % board_w
        ny = (hy + direction[1]) % board_h
        new_head = (nx, ny)
        box_target = self._box_hit_target(new_head, boxes)
        if box_target == "command":
            snake = [new_head, *snake]
            while len(snake) > 12:
                snake.pop()
            game["snake"] = snake
            game["direction"] = direction
            game["next_direction"] = direction
            game["moves"] = int(game.get("moves", 0)) + 1
            game["last_move"] = now
            self.state = self.state.with_updates(
                mode=OperatorMode.COMMAND,
                header_logo_game=game,
                status_message="snake: input-fokus gesetzt",
            )
            return
        if isinstance(box_target, FocusPane):
            game["snake"] = [new_head, *snake[:-1]]
            self._apply_snake_escape(game, target=box_target, now=now, board_h=board_h)
            return
        if isinstance(box_target, str):
            game["snake"] = [new_head, *snake[:-1]]
            self._apply_snake_section_target(game, section_id=box_target, now=now)
            return

        snake = [new_head, *snake]
        while len(snake) > 12:
            snake.pop()

        game["snake"] = snake
        game["direction"] = direction
        game["next_direction"] = direction
        game["moves"] = int(game.get("moves", 0)) + 1
        game["last_move"] = now
        next_state = self.state.with_updates(header_logo_game=game)
        self.state = self._apply_snake_ui_controls(next_state, head=new_head, board_w=board_w, board_h=board_h)

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
        section_ids = [s.id for s in SECTIONS]
        return build_snake_control_boxes(board_w, board_h, section_ids=section_ids)

    def _box_hit_target(
        self,
        head: tuple[int, int],
        boxes: list[dict[str, object]],
    ) -> FocusPane | str | None:
        x, y = head
        for box in boxes:
            x0 = int(box.get("x0", -1))
            y0 = int(box.get("y0", -1))
            x1 = int(box.get("x1", -1))
            y1 = int(box.get("y1", -1))
            if x0 <= x <= x1 and y0 <= y <= y1:
                kind = str(box.get("kind", ""))
                target = str(box.get("target", ""))
                if kind == "pane":
                    if target == "navigation":
                        return FocusPane.NAVIGATION
                    if target == "content":
                        return FocusPane.CONTENT
                    if target == "detail":
                        return FocusPane.DETAIL
                    if target == "command":
                        return "command"
                if kind == "section" and target:
                    return target
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
