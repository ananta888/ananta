from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import asyncio
import math
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.color_depth import ColorDepth

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
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
from client_surfaces.operator_tui.ai_snake_policy import apply_policy_to_payload
from client_surfaces.operator_tui.ai_snake_observation import ObservationBuffer
from client_surfaces.operator_tui.ai_snake_lm_budget import AiSnakeLmBudget
from client_surfaces.operator_tui.ai_snake_prediction import PredictionGate, build_prediction_trace, quick_predict
from client_surfaces.operator_tui.ai_snake_prediction_cache import PredictionCache
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, merge_patterns, mine_patterns_from_events
from client_surfaces.operator_tui.ai_snake_training_recorder import AiSnakeTrainingRecorder
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    read_active_profile,
    read_events,
    read_patterns,
    save_patterns,
)
from client_surfaces.operator_tui.ai_snake_worker_client import AiSnakeWorkerClient, WorkerTask
from client_surfaces.operator_tui.artifact_intent import ArtifactIntent, ArtifactIntentDetector, IntentConfidence
from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.mouse import (
    MouseEventType as NormalizedMouseEventType,
    MouseState,
    detect_mouse_support,
    normalize_mouse_state,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow
from client_surfaces.operator_tui.plugins import PluginRegistry, default_plugin_registry, resolve_item_reference
from client_surfaces.operator_tui.region_index import RegionTarget, build_region_index
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import SECTIONS, get_section

from client_surfaces.operator_tui.chat_mixin import ChatMixin
from client_surfaces.operator_tui.snake_tick_mixin import SnakeTickMixin
from client_surfaces.operator_tui.header_snake_mixin import HeaderSnakeMixin
from client_surfaces.operator_tui.mouse_artifact_mixin import MouseArtifactMixin
from client_surfaces.operator_tui.snake_heuristic_mixin import SnakeHeuristicMixin
from client_surfaces.operator_tui.snake_ops_mixin import SnakeOpsMixin
from client_surfaces.operator_tui.tutorial_ai_mixin import TutorialAiMixin

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState


class InteractiveOperatorTui(SnakeTickMixin, SnakeHeuristicMixin, SnakeOpsMixin, TutorialAiMixin, HeaderSnakeMixin, MouseArtifactMixin, ChatMixin):
    def __init__(
        self,
        state: OperatorState,
        registry: SectionAdapterRegistry | None = None,
        splash: SplashMachine | None = None,
    ) -> None:
        self._registry = registry or SectionAdapterRegistry()
        self._splash = splash
        self._plugins: PluginRegistry = default_plugin_registry()
        self._mouse_capabilities = detect_mouse_support()
        self._mouse_state = MouseState()
        self._intent_detector = ArtifactIntentDetector(
            dwell_seconds=float(os.environ.get("ANANTA_TUI_SNAKE_MOUSE_DWELL", "0.35"))
        )
        self.state = load_active_section(state, self._registry)
        term_graphics = dict(self.state.terminal_graphics or {})
        term_graphics["mouse_support"] = dict(self._mouse_capabilities)
        self.state = self.state.with_updates(terminal_graphics=term_graphics)
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
        self._codecompass_build_future: Future[Path | None] | None = None
        self._codecompass_build_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-codecompass-build")
        self._codecompass_build_output_dir: Path | None = None
        self._tutorial_last_source: str = "local-knowledge"
        self._tutorial_last_target: str = "follow"
        self._ai_observation = ObservationBuffer(max_events=100)
        self._ai_prediction_gate = PredictionGate(min_interval_seconds=3.0, min_confidence=0.35, stable_ms=500)
        self._ai_prediction_cache = PredictionCache(ttl_seconds=30)
        self._ai_lm_budget = AiSnakeLmBudget()
        self._ai_worker_client = AiSnakeWorkerClient()
        learning_cfg = dict(read_active_profile().get("learning_settings") or {})
        self._ai_learning_settings = learning_cfg
        self._ai_learning_settings_loaded_at = 0.0
        self._ai_learning_last_mined_at = 0.0
        self._ai_training_recorder = AiSnakeTrainingRecorder(enabled=bool(learning_cfg.get("enabled", True)))
        self._ai_training_recorder.set_paused(bool(learning_cfg.get("paused", False)))
        self._ai_worker_task: WorkerTask | None = None
        self._ai_last_signature = ""
        # E01/E02: new runtime state
        self._snake_idle_since: float = 0.0
        self._snake_last_event_fired: str = ""
        self._tutor_event_session_used: set[str] = set()
        self._tutor_ask_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-tutor-ask")
        self._tutor_ask_future: Future[str | None] | None = None
        self._section_first_visit_pending: str = ""
        # E01: load highscore cache once
        self._scores_cache: dict[str, Any] = {}
        try:
            from client_surfaces.operator_tui.snake_persistence import load_snake_scores
            self._scores_cache = load_snake_scores()
        except Exception:
            pass
        # E02: tutor depth mode from persistence
        self._tutor_depth_mode: str = "overview"
        try:
            from client_surfaces.operator_tui.snake_persistence import get_tutor_mode
            self._tutor_depth_mode = get_tutor_mode()
        except Exception:
            pass
        self._command_buffer = ""
        # E03: Chat transport (initialized lazily when snake registers with Hub)
        self._chat_transport: Any = None
        # Heuristic selection state
        self._codecompass_artifact_cache: tuple[float, dict[str, Any] | None] = (0.0, None)
        self._active_heuristics_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._heuristic_traces: list[Any] = []
        self._last_heuristic_proposal_at: float = 0.0
        self._selected_heuristic_id: str = ""
        # E05: Load initial notes into notes:self channel
        self._init_notes_channel()
        self._rendered_text = self._render()
        self._control = FormattedTextControl(text=lambda: ANSI(self._rendered_text))
        self._output = Window(content=self._control, wrap_lines=False)
        self._app = Application(
            layout=Layout(self._output),
            key_bindings=self._build_keybindings(),
            full_screen=True,
            mouse_support=bool(self._mouse_capabilities.get("enabled")),
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
            if self._chat_focus_active():
                self._chat_append("q")
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("q")
                return
            if self._snake_mode_active():
                return
            event.app.exit()

        @bindings.add(":")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append(":")
                return
            if self._chat_focus_active():
                self._chat_append(":")
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
            if self._artifact_chat_focus_active():
                self._artifact_chat_send_message()
                return
            if self._chat_focus_active():
                self._chat_send_message()
                return
            if self._snake_mode_active():
                # T04.04: Enter advances guided tour immediately
                game = self.state.header_logo_game or {}
                ts_raw = game.get("tutorial_state")
                if isinstance(ts_raw, dict) and ts_raw.get("guided"):
                    self._advance_guided_tour_now()
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
            if self._artifact_chat_focus_active():
                self._artifact_chat_focus_leave(clear=False)
                return
            if self._chat_focus_active():
                self._chat_focus_leave()
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
            if self._artifact_chat_focus_active():
                self._artifact_chat_backspace()
                return
            if self._chat_focus_active():
                self._chat_backspace()
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
                if self._open_selected_item_inline():
                    return
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

        @bindings.add("c-h")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_context_help()

        @bindings.add("c-k")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._send_terminal_context_to_ai()

        @bindings.add("tab")
        def _(event) -> None:
            if self._chat_focus_active() or self._artifact_chat_focus_active() or self._snake_mode_active():
                self._chat_cycle_channel()
                return
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
            if self._artifact_chat_focus_active():
                self._artifact_chat_append(" ")
                return
            if self._chat_focus_active():
                self._chat_append(" ")
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_pause()  # T01.02: Space togglet Pause statt Stopp

        @bindings.add("c-s")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_snake_mode()

        @bindings.add("c-e")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            if self._snake_mode_active():
                self._chat_focus_enter()
                return
            self._artifact_chat_focus_enter()

        @bindings.add("c-g")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_chat_panel_open()

        @bindings.add("c-l")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_clear_input()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_clear_input()

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
            if self._chat_focus_active():
                self._chat_append("m")
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
            if self._chat_focus_active():
                self._chat_append("x")
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
            if self._chat_focus_active():
                self._chat_append("b")
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
            if self._artifact_chat_focus_active():
                self._artifact_chat_append("c")
                return
            if not self._snake_mode_active() and self._chat_panel_available():
                self._artifact_chat_focus_enter()
                return
            if not self._snake_mode_active():
                return
            if self._chat_focus_active():
                self._chat_append("c")
                return
            self._chat_focus_enter()

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
            if self._chat_focus_active():
                self._chat_append("v")
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
            if self._chat_focus_active():
                self._chat_append("t")
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
            if self._chat_focus_active():
                self._chat_append("y")
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
            if self._chat_focus_active():
                self._chat_append("z")
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
            if self._chat_focus_active():
                self._chat_append("u")
                return
            self._toggle_tutorial_ai_mode()

        @bindings.add("o")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("o")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("o")
                return
            if not self._snake_mode_active():
                return
            if self._chat_focus_active():
                self._chat_append("o")
                return
            self._toggle_snake_mouse_follow()

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

        # ── AI-snake feedback keys (ASH-041) ──────────────────────────────────
        # Conflict analysis: r=refresh, p=prev_section → use R (Shift+r) and P (Shift+p)
        # + and - are unbound.

        @bindings.add("+")
        def _(event) -> None:
            """Positive feedback for current snake heuristic behavior."""
            if self._chat_focus_active() or self._artifact_chat_focus_active():
                self._normal_or_text("+", lambda: None)
                return
            self._snake_feedback(positive=True)

        @bindings.add("-")
        def _(event) -> None:
            """Negative feedback for current snake heuristic behavior."""
            if self._chat_focus_active() or self._artifact_chat_focus_active():
                self._normal_or_text("-", lambda: None)
                return
            self._snake_feedback(positive=False)

        @bindings.add("R")   # Shift+r — rollback (r is already :refresh)
        def _(event) -> None:
            """Rollback current auto-promoted snake heuristic."""
            if self._chat_focus_active() or self._artifact_chat_focus_active():
                self._normal_or_text("R", lambda: None)
                return
            self._snake_rollback_heuristic()

        @bindings.add("P")   # Shift+p — pin (p is already :prev)
        def _(event) -> None:
            """Pin current heuristic — prevent automatic replacement."""
            if self._chat_focus_active() or self._artifact_chat_focus_active():
                self._normal_or_text("P", lambda: None)
                return
            self._snake_pin_heuristic()

        @bindings.add("<any>")
        def _(event) -> None:
            if self._snake_message_mode_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._snake_message_append(data)
                return
            if self._artifact_chat_focus_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._artifact_chat_append(data)
                return
            if self._chat_focus_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._chat_append(data)
                return
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._append_command(data)

        @bindings.add("pageup")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(-5)

        @bindings.add("pagedown")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(5)

        @bindings.add("escape", "up")  # Alt+Up
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(-1)

        @bindings.add("escape", "down")  # Alt+Down
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(1)

        @bindings.add("escape", "1")
        def _(event) -> None:
            self._chat_switch_channel("room:main")

        @bindings.add("escape", "2")
        def _(event) -> None:
            self._chat_switch_channel("ai:tutor")

        @bindings.add("escape", "3")
        def _(event) -> None:
            self._chat_switch_channel("notes:self")

        @bindings.add(Keys.Vt100MouseEvent)
        def _(event) -> None:
            data = event.key_sequence[0].data or ""
            parsed = self._parse_sgr_mouse_event(data)
            if parsed is None:
                return
            self._ingest_mouse_event(
                x=parsed[0],
                y=parsed[1],
                event_type=parsed[2],
                buttons=parsed[3],
                scroll_delta=parsed[4],
            )

        return bindings

    # ── Chat focus helpers (E01.04) ───────────────────────────────────────────

    def _chat_focus_active(self) -> bool:
        game = self.state.header_logo_game or {}
        chat_raw = game.get("chat_state")
        return isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus")) and (
            self._snake_mode_active() or bool(game.get("chat_panel_open"))
        )

    def _chat_panel_available(self) -> bool:
        game = self.state.header_logo_game or {}
        artifact_chat = game.get("artifact_chat_state")
        return bool(game.get("chat_panel_open")) or (
            isinstance(artifact_chat, dict) and isinstance(artifact_chat.get("active_target"), dict)
        )

    def _artifact_chat_focus_active(self) -> bool:
        game = self.state.header_logo_game or {}
        return bool(game.get("artifact_chat_focus")) and not self._snake_mode_active()

    def _toggle_chat_panel_open(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["chat_panel_open"] = not bool(game.get("chat_panel_open"))
        self._append_ai_monitor_log(
            game,
            event="chat_panel_toggled",
            label="AI-Chat aktiviert" if bool(game["chat_panel_open"]) else "AI-Chat deaktiviert",
        )
        if not game["chat_panel_open"]:
            game["artifact_chat_focus"] = False
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="chat panel: an" if game["chat_panel_open"] else "chat panel: aus",
            )
        )

    def _toggle_context_help(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["shortcut_help_open"] = not bool(game.get("shortcut_help_open"))
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="shortcuts: an" if game["shortcut_help_open"] else "shortcuts: aus",
            )
        )

    def _send_terminal_context_to_ai(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(self._rendered_text or ""))
        snapshot = "\n".join(plain.splitlines()[-120:])[:8000]
        if not snapshot.strip():
            self._set_state(self.state.with_updates(status_message="AI-Kontext: kein Terminalinhalt"))
            return
        game["ai_terminal_context"] = snapshot
        artifact_chat = dict(game.get("artifact_chat_state") or {})
        artifact_chat["active_target"] = {
            "kind": "terminal_snapshot",
            "label": "Terminal Snapshot",
            "path": "",
            "id": "terminal-current",
            "section_id": str(self.state.section_id or ""),
        }
        messages = [dict(m) for m in (artifact_chat.get("messages") or []) if isinstance(m, dict)]
        messages.append({"at": time.time(), "source": "system", "text": "Terminalinhalt als AI-Kontext übernommen."})
        artifact_chat["messages"] = messages[-12:]
        game["artifact_chat_state"] = artifact_chat
        game["chat_panel_open"] = True
        game["artifact_chat_focus"] = False

        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
        chat = get_chat_state(game)
        switch_channel(chat, "ai:tutor", preserve_input=True)
        chat["chat_focus"] = True
        set_chat_state(game, chat)
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="AI-Kontext: Terminalinhalt bereit; Frage im AI-Chat eingeben",
            )
        )

    def _chat_cycle_channel(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
        chat = get_chat_state(game)
        channels = list((chat.get("channels") or {}).keys())
        preferred = [ch for ch in ["room:main", "ai:tutor", "notes:self"] if ch in channels]
        ordered = preferred + [ch for ch in channels if ch not in preferred and ch != "system"]
        if not ordered:
            return
        current = str(chat.get("active_channel") or ordered[0])
        try:
            idx = ordered.index(current)
        except ValueError:
            idx = -1
        next_id = ordered[(idx + 1) % len(ordered)]
        switch_channel(chat, next_id, preserve_input=True)
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"kanal: {next_id}"))

    def _chat_switch_channel(self, channel_id: str) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
        chat = get_chat_state(game)
        if switch_channel(chat, channel_id, preserve_input=True):
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"kanal: {channel_id}"))

    def _chat_focus_enter(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = True
        chat["chat_input_buffer"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: focus"))

    def _chat_focus_leave(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = False
        chat["chat_input_buffer"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: game focus"))

    def _chat_append(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        chat["chat_input_buffer"] = (buf + ch)[:200]
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        chat["chat_input_buffer"] = buf[:-1]
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_clear_input(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_input_buffer"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: input cleared"))

    def _artifact_chat_focus_enter(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if not self._chat_panel_available():
            return
        game["artifact_chat_focus"] = True
        game.setdefault("artifact_chat_input", "")
        game["chat_panel_open"] = True
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: focus"))

    def _artifact_chat_focus_leave(self, *, clear: bool = False) -> None:
        game = dict(self.state.header_logo_game or {})
        game["artifact_chat_focus"] = False
        if clear:
            game["artifact_chat_input"] = ""
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: closed"))

    def _artifact_chat_append(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        game["artifact_chat_input"] = (buf + ch)[:500]
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        game["artifact_chat_input"] = buf[:-1]
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_clear_input(self) -> None:
        game = dict(self.state.header_logo_game or {})
        game["artifact_chat_input"] = ""
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: input cleared"))

    def _artifact_chat_send_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        text = str(game.get("artifact_chat_input") or "").strip()
        if not text:
            return
        game["artifact_chat_input"] = ""
        artifact_chat = dict(game.get("artifact_chat_state") or {})
        messages = [dict(m) for m in (artifact_chat.get("messages") or []) if isinstance(m, dict)]
        messages.append({"at": time.time(), "source": "user", "text": text})
        artifact_chat["messages"] = messages[-12:]
        game["artifact_chat_state"] = artifact_chat
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel, append_message, make_message
        chat = get_chat_state(game)
        switch_channel(chat, "ai:tutor", preserve_input=True)
        msg = make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id=str(game.get("local_snake_id") or "s1"),
            sender_kind="user",
            text=text,
            visibility="ai_context",
            delivery_state="sent",
        )
        append_message(chat, msg)
        set_chat_state(game, chat)
        game["tutor_ask_question"] = text
        game["tutor_ask_at"] = time.monotonic()
        game["tutor_ask_answered"] = False
        game["_ask_submitted"] = False
        game["active"] = True
        game["alive"] = True
        game["tutorial_mode"] = True
        game["ui_steering"] = False
        game["free_mode"] = False
        chat["ai_typing"] = True
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"ask: {text[:40]}"))

    def _chat_scroll(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        current = int(chat.get("scroll_offset") or 0)
        chat["scroll_offset"] = max(0, current + delta)
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _normal_or_text(self, text: str, normal_action) -> None:
        if self._snake_message_mode_active():
            self._snake_message_append(text)
            return
        if self._artifact_chat_focus_active():
            self._artifact_chat_append(text)
            return
        if self.state.mode is OperatorMode.COMMAND:
            self._append_command(text)
            return
        if self._chat_focus_active():
            self._chat_append(text)
            return
        if self._snake_mode_active():
            return
        normal_action()

    def _append_command(self, text: str) -> None:
        self._command_buffer += text
        self._set_state(self.state.with_updates(command_line=self._command_buffer))

    def _toggle_snake_mouse_follow(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("mouse_follow_enabled"))
        game["mouse_follow_enabled"] = not enabled
        game["movement_mode"] = "mouse_follow" if not enabled else "keyboard"
        status = "an" if not enabled else "aus"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake mouse-follow: {status}"))

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
