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
from client_surfaces.operator_tui.keybindings_config import key_for_action
from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_filter_options,
    ai_snake_config_items,
    ai_snake_config_options,
    apply_ai_snake_config_value,
    refresh_chat_backend_models,
)
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
from client_surfaces.operator_tui.visual.adapters.ansi_adapter import AnsiOutputAdapter
from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
from client_surfaces.operator_tui.visual.adapters.noop_adapter import NoopDiagnosticsAdapter
from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.renderers.ansi_renderer import AnsiBlocksRenderer
from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
from client_surfaces.operator_tui.visual.renderers.svg_raster_renderer import SvgRasterRenderer
from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.registry import OutputAdapterRegistry, RendererRegistry, ViewRegistry
from client_surfaces.operator_tui.visual.runtime.visual_runtime import VisualRuntime
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion, derive_pixel_size
from client_surfaces.operator_tui.visual.views.artifact_preview_view import ArtifactPreviewView
from client_surfaces.operator_tui.visual.views.logo_animation_view import LogoAnimationView
from client_surfaces.operator_tui.visual.views.renderer_diagnostics_view import RendererDiagnosticsView
from client_surfaces.operator_tui.visual.views.snake_debug_view import SnakeDebugView
from client_surfaces.operator_tui.visual.views.strategy_map_preview_view import StrategyMapPreviewView

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
        self._command_buffer = str(self.state.command_line or "") if self.state.mode is OperatorMode.COMMAND else ""
        self._command_cursor = len(self._command_buffer)
        self._command_history: list[str] = []
        self._command_history_index: int | None = None
        self._command_saved_draft = ""
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
        self._visual_config_error: str = ""
        self._visual_viewport_config = self._load_visual_viewport_config()
        self._visual_runtime: VisualRuntime | None = None
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

        @bindings.add(key_for_action("quit", "c-q"))
        def _(event) -> None:
            self._handle_quit_key(event)

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
            self._open_command_mode()

        @bindings.add("/")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append("/")
                return
            if self._chat_focus_active():
                self._chat_focus_leave()
                self._open_command_mode()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_focus_leave(clear=False)
                self._open_command_mode()
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("/")
                return

        @bindings.add("enter")
        def _(event) -> None:
            self._handle_enter_key()

        @bindings.add("escape")
        def _(event) -> None:
            self._escape_to_start_state()

        @bindings.add("backspace")
        @bindings.add("c-h")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_backspace()
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_backspace()
                return
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

        @bindings.add("delete")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_delete()
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_delete()
                return
            if self._snake_message_mode_active():
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_delete()
                return
            if self._chat_focus_active():
                self._chat_delete()
                return
            if self._snake_mode_active():
                return

        @bindings.add(key_for_action("selection_down", "c-j"))
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(1, game)))
                return
            def _j():
                self._set_state(self.state.with_updates(selected_index=self._clamp_down()))
            self._normal_or_text("j", _j)

        @bindings.add(key_for_action("selection_up", "c-k"))
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(-1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(-1, game)))
                return
            self._normal_or_text("k", lambda: self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1))))

        @bindings.add(key_for_action("inspect", "c-f"))
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

        @bindings.add(key_for_action("focus_left", "c-a"))
        def _(event) -> None:
            self._normal_or_text("h", lambda: self._move_focus(-1))

        @bindings.add(key_for_action("focus_right", "c-d"))
        def _(event) -> None:
            self._normal_or_text("l", lambda: self._move_focus(1))

        @bindings.add(key_for_action("refresh", "c-r"))
        def _(event) -> None:
            self._normal_or_text("r", lambda: self._run_command(":refresh"))

        @bindings.add(key_for_action("help", "c-y"))
        def _(event) -> None:
            self._normal_or_text("?", lambda: self._run_command(":help"))

        @bindings.add(key_for_action("toggle_shortcut_help", "c-]"))
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
            if self.state.mode is OperatorMode.COMMAND:
                self._command_backspace()
                return
            self._toggle_context_help()

        @bindings.add(key_for_action("send_terminal_context", "c-t"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._send_terminal_context_to_ai()

        @bindings.add(key_for_action("cycle_focus_or_channel", "c-w"))
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

        @bindings.add(key_for_action("snake_pause", "c-p"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_pause()  # T01.02: Space togglet Pause statt Stopp

        @bindings.add(key_for_action("toggle_snake_mode", "c-s"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_snake_mode()

        @bindings.add(key_for_action("chat_focus", "c-e"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_chat_focus()

        @bindings.add(key_for_action("toggle_chat_panel", "c-g"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_chat_panel_open()

        @bindings.add(key_for_action("copy_chat_panel", "c-c"))
        def _(event) -> None:
            if self._cancel_active_input_mode():
                return
            self._copy_chat_panel_snapshot()

        @bindings.add(key_for_action("copy_ai_status", "c-i"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._copy_ai_status_snapshot()

        @bindings.add(key_for_action("clear_chat_input", "c-l"))
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_clear_input()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_clear_input()

        @bindings.add(key_for_action("snake_toggle_selection", "c-x"))
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

        @bindings.add(key_for_action("snake_toggle_frame", "c-b"))
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

        @bindings.add(key_for_action("snake_replace_selection", "c-v"))
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

        @bindings.add(key_for_action("snake_clear_marks", "c-z"))
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

        @bindings.add(key_for_action("toggle_tutorial_ai", "c-u"))
        def _(event) -> None:
            self._toggle_tutorial_ai_mode()

        @bindings.add(key_for_action("toggle_ai_snake_config", "f6"))
        def _(event) -> None:
            self._toggle_ai_snake_config_panel()

        @bindings.add(key_for_action("toggle_mouse_follow", "c-o"))
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
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_move_cursor(-1)
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_move_cursor(-1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_move_cursor(-1)
                return
            if self._chat_focus_active():
                self._chat_move_cursor(-1)
                return
            if self._try_header_snake_direction((-1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("right")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_move_cursor(1)
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_move_cursor(1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_move_cursor(1)
                return
            if self._chat_focus_active():
                self._chat_move_cursor(1)
                return
            if self._try_header_snake_direction((1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add("up")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(-1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(-1, game)))
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_history_move(-1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_history_move(-1)
                return
            if self._chat_focus_active():
                self._chat_history_move(-1)
                return
            if self._try_header_snake_direction((0, -1)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("down")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(1, game)))
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_history_move(1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_history_move(1)
                return
            if self._chat_focus_active():
                self._chat_history_move(1)
                return
            if self._try_header_snake_direction((0, 1)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add(key_for_action("next_section", "c-n"))
        def _(event) -> None:
            self._normal_or_text("n", lambda: self._run_command(":next"))

        @bindings.add("<any>")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._ai_snake_config_combo_append_filter(data)
                return
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data in {"\b", "\x7f"}:
                    self._command_backspace()
                    return
                if data and data.isprintable():
                    self._append_command(data)
                return
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

        @bindings.add("pageup")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(-5)

        @bindings.add("pagedown")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(5)

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
        chat["chat_input_cursor"] = len(str(chat.get("chat_input_buffer") or ""))
        chat["chat_input_history_index"] = None
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
        chat["chat_input_cursor"] = 0
        chat["chat_input_history_index"] = None
        chat["chat_input_saved_draft"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: focus"))

    def _chat_focus_leave(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = False
        chat["chat_input_buffer"] = ""
        chat["chat_input_cursor"] = 0
        chat["chat_input_history_index"] = None
        chat["chat_input_saved_draft"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: game focus"))

    def _toggle_chat_focus(self) -> None:
        if self._chat_focus_active():
            self._chat_focus_leave()
            return
        if self._artifact_chat_focus_active():
            self._artifact_chat_focus_leave(clear=False)
            return
        if self._snake_mode_active():
            self._chat_focus_enter()
            return
        self._artifact_chat_focus_enter()

    def _chat_append(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
        if len(buf) >= 200:
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        new_buf = (buf[:cursor] + ch + buf[cursor:])[:200]
        new_cursor = min(len(new_buf), cursor + len(ch))
        chat["chat_input_buffer"] = new_buf
        chat["chat_input_cursor"] = new_cursor
        chat["chat_input_history_index"] = None
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
        if cursor <= 0:
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        chat["chat_input_buffer"] = buf[:cursor - 1] + buf[cursor:]
        chat["chat_input_cursor"] = cursor - 1
        chat["chat_input_history_index"] = None
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_delete(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
        if cursor >= len(buf):
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        chat["chat_input_buffer"] = buf[:cursor] + buf[cursor + 1:]
        chat["chat_input_cursor"] = cursor
        chat["chat_input_history_index"] = None
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_move_cursor(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
        chat["chat_input_cursor"] = max(0, min(len(buf), cursor + int(delta)))
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_history_move(self, step: int) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        history = [str(item) for item in (chat.get("chat_input_history") or []) if str(item).strip()]
        if not history:
            return
        buf = str(chat.get("chat_input_buffer") or "")
        idx_raw = chat.get("chat_input_history_index")
        idx = int(idx_raw) if isinstance(idx_raw, int) else None

        if int(step) < 0:
            if idx is None:
                chat["chat_input_saved_draft"] = buf
                idx = len(history) - 1
            else:
                idx = max(0, idx - 1)
            selected = history[idx]
            chat["chat_input_buffer"] = selected
            chat["chat_input_cursor"] = len(selected)
            chat["chat_input_history_index"] = idx
        else:
            if idx is None:
                set_chat_state(game, chat)
                self._set_state(self.state.with_updates(header_logo_game=game))
                return
            if idx < len(history) - 1:
                idx += 1
                selected = history[idx]
                chat["chat_input_buffer"] = selected
                chat["chat_input_cursor"] = len(selected)
                chat["chat_input_history_index"] = idx
            else:
                draft = str(chat.get("chat_input_saved_draft") or "")
                chat["chat_input_buffer"] = draft
                chat["chat_input_cursor"] = len(draft)
                chat["chat_input_history_index"] = None

        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_clear_input(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_input_buffer"] = ""
        chat["chat_input_cursor"] = 0
        chat["chat_input_history_index"] = None
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: input cleared"))

    def _artifact_chat_focus_enter(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if not self._chat_panel_available():
            return
        game["artifact_chat_focus"] = True
        game.setdefault("artifact_chat_input", "")
        game["artifact_chat_cursor"] = max(0, min(len(str(game.get("artifact_chat_input") or "")), int(game.get("artifact_chat_cursor") or len(str(game.get("artifact_chat_input") or "")))))
        game["artifact_chat_history_index"] = None
        game.setdefault("artifact_chat_history", [])
        game.setdefault("artifact_chat_saved_draft", "")
        game["chat_panel_open"] = True
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: focus"))

    def _artifact_chat_focus_leave(self, *, clear: bool = False) -> None:
        game = dict(self.state.header_logo_game or {})
        game["artifact_chat_focus"] = False
        if clear:
            game["artifact_chat_input"] = ""
            game["artifact_chat_cursor"] = 0
            game["artifact_chat_history_index"] = None
            game["artifact_chat_saved_draft"] = ""
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: closed"))

    def _artifact_chat_append(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
        if len(buf) >= 500:
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        new_buf = (buf[:cursor] + ch + buf[cursor:])[:500]
        game["artifact_chat_input"] = new_buf
        game["artifact_chat_cursor"] = min(len(new_buf), cursor + len(ch))
        game["artifact_chat_history_index"] = None
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
        if cursor <= 0:
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        game["artifact_chat_input"] = buf[:cursor - 1] + buf[cursor:]
        game["artifact_chat_cursor"] = cursor - 1
        game["artifact_chat_history_index"] = None
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_delete(self) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
        if cursor >= len(buf):
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        game["artifact_chat_input"] = buf[:cursor] + buf[cursor + 1:]
        game["artifact_chat_cursor"] = cursor
        game["artifact_chat_history_index"] = None
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_move_cursor(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or {})
        buf = str(game.get("artifact_chat_input") or "")
        cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
        game["artifact_chat_cursor"] = max(0, min(len(buf), cursor + int(delta)))
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_history_move(self, step: int) -> None:
        game = dict(self.state.header_logo_game or {})
        history = [str(item) for item in (game.get("artifact_chat_history") or []) if str(item).strip()]
        if not history:
            return
        buf = str(game.get("artifact_chat_input") or "")
        idx_raw = game.get("artifact_chat_history_index")
        idx = int(idx_raw) if isinstance(idx_raw, int) else None
        if int(step) < 0:
            if idx is None:
                game["artifact_chat_saved_draft"] = buf
                idx = len(history) - 1
            else:
                idx = max(0, idx - 1)
            selected = history[idx]
            game["artifact_chat_input"] = selected
            game["artifact_chat_cursor"] = len(selected)
            game["artifact_chat_history_index"] = idx
        else:
            if idx is None:
                self._set_state(self.state.with_updates(header_logo_game=game))
                return
            if idx < len(history) - 1:
                idx += 1
                selected = history[idx]
                game["artifact_chat_input"] = selected
                game["artifact_chat_cursor"] = len(selected)
                game["artifact_chat_history_index"] = idx
            else:
                draft = str(game.get("artifact_chat_saved_draft") or "")
                game["artifact_chat_input"] = draft
                game["artifact_chat_cursor"] = len(draft)
                game["artifact_chat_history_index"] = None
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _artifact_chat_clear_input(self) -> None:
        game = dict(self.state.header_logo_game or {})
        game["artifact_chat_input"] = ""
        game["artifact_chat_cursor"] = 0
        game["artifact_chat_history_index"] = None
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="artifact chat: input cleared"))

    def _artifact_chat_send_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        text = str(game.get("artifact_chat_input") or "").strip()
        if not text:
            return
        game["artifact_chat_input"] = ""
        game["artifact_chat_cursor"] = 0
        history = [str(item) for item in (game.get("artifact_chat_history") or []) if str(item).strip()]
        if not history or history[-1] != text:
            history.append(text)
        game["artifact_chat_history"] = history[-50:]
        game["artifact_chat_history_index"] = None
        game["artifact_chat_saved_draft"] = ""
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
        timeout_s = self._chat_ask_timeout_seconds()
        game["tutor_ask_timeout_s"] = timeout_s
        game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
        game["tutor_ask_answered"] = False
        game["_ask_submitted"] = False
        game["active"] = True
        game["alive"] = True
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

    def _copy_chat_panel_snapshot(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, sanitize_text
        chat = get_chat_state(game)
        active_ch_id = str(chat.get("active_channel") or "room:main")
        channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
        ch = channels.get(active_ch_id) if isinstance(channels, dict) else {}
        if not isinstance(ch, dict):
            ch = {}
        display_name = str(ch.get("display_name") or active_ch_id)
        lines = [f"CHAT {display_name} ({active_ch_id})"]
        msgs = [m for m in (ch.get("messages") or []) if isinstance(m, dict)]
        for msg in msgs[-80:]:
            sender_kind = str(msg.get("sender_kind") or "user")
            sender_id = str(msg.get("sender_id") or "?")
            if sender_kind == "ai" or sender_id == "s-ai":
                sender = "AI-snake"
            elif sender_kind == "system":
                sender = "system"
            else:
                sender = "user"
            created_at = msg.get("created_at")
            if isinstance(created_at, (int, float)):
                ts = time.strftime("%H:%M", time.localtime(float(created_at)))
            else:
                ts = "--:--"
            text = sanitize_text(str(msg.get("text") or ""))
            if text:
                lines.append(f"[{ts}] {sender}: {text}")
        copied = "\n".join(lines).strip()
        game["clipboard"] = copied
        ok = self._copy_to_system_clipboard(copied) if copied else False
        status = "chat copy: intern + System-Zwischenablage" if ok else "chat copy: intern"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    def _copy_ai_status_snapshot(self) -> None:
        game = dict(self.state.header_logo_game or {})
        lines = ["AI-SNAKE STATUS"]
        lines.append(f"tutorial_mode={bool(game.get('tutorial_mode'))}")
        lines.append(f"chat_panel_open={bool(game.get('chat_panel_open'))}")
        lines.append(f"ai_snake_mode={str(game.get('ai_snake_mode') or 'lurking_follow')}")
        lines.append(f"runtime_status={str(game.get('ai_snake_runtime_status') or 'idle')}")
        lines.append(f"provider={str(game.get('ai_snake_provider_preference') or 'lmstudio')}")
        lines.append(f"model={str(game.get('ai_snake_provider_model') or 'ananta-smoke')}")
        monitor = game.get("ai_snake_monitor_log")
        rows = [dict(item) for item in monitor if isinstance(item, dict)] if isinstance(monitor, list) else []
        if rows:
            lines.append("events:")
            for item in rows[-20:]:
                created_at = item.get("created_at")
                ts = time.strftime("%H:%M", time.localtime(float(created_at))) if isinstance(created_at, (int, float)) else "--:--"
                label = str(item.get("label") or item.get("event") or "event")
                lines.append(f"- {ts} {label}")
        copied = "\n".join(lines).strip()
        game["clipboard"] = copied
        ok = self._copy_to_system_clipboard(copied) if copied else False
        status = "ai status copy: intern + System-Zwischenablage" if ok else "ai status copy: intern"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

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

    def _handle_enter_key(self) -> None:
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        if bool(game.get("ai_snake_config_open")):
            if self.state.focus is not FocusPane.CONTENT:
                self._set_state(self.state.with_updates(focus=FocusPane.CONTENT))
                game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_commit()
            else:
                self._toggle_ai_snake_config_selected()
            return
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
            self._command_commit_history()
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

    def _cancel_active_input_mode(self) -> bool:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if self._ai_snake_config_combo_active(game):
            self._ai_snake_config_combo_close(status="input: config-auswahl beendet")
            return True
        if self.state.mode is OperatorMode.COMMAND:
            self._command_reset()
            self._set_state(self.state.with_updates(mode=OperatorMode.NORMAL, status_message="input: command beendet"))
            return True
        if self._snake_message_mode_active():
            self._snake_cancel_message()
            return True
        return False

    def _append_command(self, text: str) -> None:
        cursor = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        self._command_buffer = self._command_buffer[:cursor] + text + self._command_buffer[cursor:]
        self._command_cursor = min(len(self._command_buffer), cursor + len(text))
        self._command_history_index = None
        self._sync_command_line_state()

    def _command_backspace(self) -> None:
        if not self._command_buffer:
            game = dict(self.state.header_logo_game or {})
            game["command_input_cursor"] = 0
            self._command_cursor = 0
            self._command_history_index = None
            self._command_saved_draft = ""
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message="command: beendet",
                )
            )
            return
        cursor = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        if cursor <= 0:
            self._sync_command_line_state()
            return
        self._command_buffer = self._command_buffer[:cursor - 1] + self._command_buffer[cursor:]
        self._command_cursor = max(0, cursor - 1)
        self._command_history_index = None
        self._sync_command_line_state()

    def _command_delete(self) -> None:
        cursor = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        if cursor >= len(self._command_buffer):
            self._sync_command_line_state()
            return
        self._command_buffer = self._command_buffer[:cursor] + self._command_buffer[cursor + 1:]
        self._command_history_index = None
        self._sync_command_line_state()

    def _command_move_cursor(self, delta: int) -> None:
        cursor = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        self._command_cursor = max(0, min(len(self._command_buffer), cursor + int(delta)))
        self._sync_command_line_state()

    def _command_history_move(self, delta: int) -> None:
        history = [str(item) for item in self._command_history if str(item).strip()]
        if not history:
            return
        idx_raw = self._command_history_index
        if idx_raw is None:
            self._command_saved_draft = self._command_buffer
            idx = len(history)
        else:
            idx = max(0, min(len(history), int(idx_raw)))
        next_idx = idx + int(delta)
        if next_idx < 0:
            next_idx = 0
        if next_idx >= len(history):
            self._command_history_index = None
            self._command_buffer = self._command_saved_draft
            self._command_cursor = len(self._command_buffer)
            self._sync_command_line_state()
            return
        self._command_history_index = next_idx
        self._command_buffer = history[next_idx]
        self._command_cursor = len(self._command_buffer)
        self._sync_command_line_state()

    def _command_commit_history(self) -> None:
        text = str(self._command_buffer).strip()
        if not text:
            return
        if not self._command_history or self._command_history[-1] != text:
            self._command_history.append(text)
        self._command_history = self._command_history[-80:]
        self._command_history_index = None
        self._command_saved_draft = ""

    def _command_reset(self) -> None:
        self._command_buffer = ""
        self._command_cursor = 0
        self._command_history_index = None
        self._command_saved_draft = ""
        self._sync_command_line_state()

    def _open_command_mode(self) -> None:
        game = dict(self.state.header_logo_game or {})
        self._command_buffer = ""
        self._command_cursor = 0
        self._command_history_index = None
        self._command_saved_draft = ""
        game["command_input_cursor"] = self._command_cursor
        self._set_state(self.state.with_updates(header_logo_game=game, mode=OperatorMode.COMMAND, command_line=self._command_buffer))

    def _sync_command_line_state(self) -> None:
        game = dict(self.state.header_logo_game or {})
        game["command_input_cursor"] = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        self._set_state(self.state.with_updates(header_logo_game=game, command_line=self._command_buffer))

    def _toggle_ai_snake_config_panel(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        opened = not bool(game.get("ai_snake_config_open"))
        game["ai_snake_config_open"] = opened
        if opened:
            game["artifact_chat_focus"] = False
            from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
            chat = get_chat_state(game)
            chat["chat_focus"] = False
            set_chat_state(game, chat)
            self._command_buffer = ""
            self._command_cursor = 0
            self._command_history_index = None
            self._command_saved_draft = ""
            game["ai_snake_config_combo"] = {
                "open": False,
                "key": "",
                "filter": "",
                "filter_cursor": 0,
                "selected_option": 0,
            }
            self._set_state(self.state.with_updates(
                header_logo_game=game,
                focus=FocusPane.CONTENT,
                mode=OperatorMode.NORMAL,
                command_line="",
                selected_index=0,
                status_message="ai config: offen",
            ))
            return
        game["ai_snake_config_combo"] = {"open": False}
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="ai config: geschlossen"))

    def _toggle_ai_snake_config_selected(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        items = ai_snake_config_items(game)
        if not items:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="ai config: keine felder"))
            return
        idx = max(0, min(len(items) - 1, int(self.state.selected_index)))
        key = str(items[idx].get("key") or "")
        self._open_ai_snake_config_combo(game, key=key, idx=idx)

    def _ai_snake_config_combo_active(self, game: dict[str, object] | None = None) -> bool:
        snapshot = game if isinstance(game, dict) else dict(self.state.header_logo_game or {})
        combo = snapshot.get("ai_snake_config_combo")
        return isinstance(combo, dict) and bool(combo.get("open"))

    def _ai_snake_config_next_index(self, delta: int, game: dict[str, object] | None = None) -> int:
        snapshot = game if isinstance(game, dict) else dict(self.state.header_logo_game or {})
        items = ai_snake_config_items(snapshot)
        if not items:
            return 0
        cur = max(0, min(len(items) - 1, int(self.state.selected_index)))
        return max(0, min(len(items) - 1, cur + int(delta)))

    def _open_ai_snake_config_combo(self, game: dict[str, object], *, key: str, idx: int) -> None:
        if key == "chat_model":
            _, fetch_error = refresh_chat_backend_models(game, force=True)
        else:
            fetch_error = ""
        options = ai_snake_config_options(game, key=key)
        if not options:
            status = "ai config: keine optionen"
            if key == "chat_model" and fetch_error:
                status = f"ai config: chat model fetch fehlgeschlagen ({fetch_error})"
            self._set_state(self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=status))
            return
        game["ai_snake_config_combo"] = {
            "open": True,
            "key": key,
            "filter": "",
            "filter_cursor": 0,
            "selected_option": 0,
        }
        self._set_state(self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=f"ai config: auswahl für {key}"))

    def _ai_snake_config_combo_close(self, *, status: str = "ai config: auswahl geschlossen") -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["ai_snake_config_combo"] = {"open": False}
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    def _ai_snake_config_combo_filter_text(self, combo: dict[str, object]) -> str:
        return str(combo.get("filter") or "")

    def _ai_snake_config_combo_apply(self, game: dict[str, object], *, value: str) -> None:
        combo = dict(game.get("ai_snake_config_combo") or {})
        key = str(combo.get("key") or "")
        idx = max(0, int(self.state.selected_index))
        status = apply_ai_snake_config_value(game, key=key, value=value)
        game["ai_snake_config_combo"] = {"open": False}
        if key == "visual_enabled" and not bool(game.get("tutorial_mode")):
            self._disable_visual_ai_snake_runtime(game)
            game["ai_snake_config_open"] = True
        self._set_state(self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=status))

    def _ai_snake_config_combo_commit(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        key = str(combo.get("key") or "")
        filter_text = self._ai_snake_config_combo_filter_text(combo)
        options, _ = ai_snake_config_filter_options(game, key=key, regex_filter=filter_text)
        if filter_text.strip():
            self._ai_snake_config_combo_apply(game, value=filter_text.strip())
            return
        if not options:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="ai config: keine treffer"))
            return
        selected = max(0, min(len(options) - 1, int(combo.get("selected_option") or 0)))
        self._ai_snake_config_combo_apply(game, value=options[selected])

    def _ai_snake_config_combo_move(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        if not bool(combo.get("open")):
            return
        key = str(combo.get("key") or "")
        options, _ = ai_snake_config_filter_options(game, key=key, regex_filter=self._ai_snake_config_combo_filter_text(combo))
        if not options:
            combo["selected_option"] = 0
        else:
            cur = max(0, min(len(options) - 1, int(combo.get("selected_option") or 0)))
            combo["selected_option"] = max(0, min(len(options) - 1, cur + int(delta)))
        game["ai_snake_config_combo"] = combo
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _ai_snake_config_combo_append_filter(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        if not bool(combo.get("open")):
            return
        buf = str(combo.get("filter") or "")
        cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
        next_buf = buf[:cursor] + ch + buf[cursor:]
        combo["filter"] = next_buf
        combo["filter_cursor"] = min(len(next_buf), cursor + len(ch))
        combo["selected_option"] = 0
        game["ai_snake_config_combo"] = combo
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _ai_snake_config_combo_backspace(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        if not bool(combo.get("open")):
            return
        buf = str(combo.get("filter") or "")
        cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
        if cursor <= 0:
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        combo["filter"] = buf[:cursor - 1] + buf[cursor:]
        combo["filter_cursor"] = cursor - 1
        combo["selected_option"] = 0
        game["ai_snake_config_combo"] = combo
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _ai_snake_config_combo_delete(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        if not bool(combo.get("open")):
            return
        buf = str(combo.get("filter") or "")
        cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
        if cursor >= len(buf):
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        combo["filter"] = buf[:cursor] + buf[cursor + 1:]
        combo["filter_cursor"] = cursor
        combo["selected_option"] = 0
        game["ai_snake_config_combo"] = combo
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _ai_snake_config_combo_move_cursor(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        combo = dict(game.get("ai_snake_config_combo") or {})
        if not bool(combo.get("open")):
            return
        buf = str(combo.get("filter") or "")
        cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
        combo["filter_cursor"] = max(0, min(len(buf), cursor + int(delta)))
        game["ai_snake_config_combo"] = combo
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _ai_snake_config_combo_select_value(self, *, value: str) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        self._ai_snake_config_combo_apply(game, value=value)

    def _handle_quit_key(self, event) -> None:
        event.app.exit()

    def _toggle_snake_mouse_follow(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("mouse_follow_enabled"))
        game["mouse_follow_enabled"] = not enabled
        game["movement_mode"] = "mouse_follow" if not enabled else "keyboard"
        status = "an" if not enabled else "aus"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake mouse-follow: {status}"))

    def _escape_to_start_state(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

        chat = get_chat_state(game)
        chat["chat_focus"] = False
        chat["chat_input_buffer"] = ""
        chat["chat_input_cursor"] = 0
        chat["chat_input_history_index"] = None
        chat["chat_input_saved_draft"] = ""
        set_chat_state(game, chat)

        game["artifact_chat_focus"] = False
        game["ai_snake_config_open"] = False
        game["ai_snake_config_combo"] = {"open": False}
        game["snake_message_mode"] = False
        game["snake_message_input"] = ""
        game["snake_message_cursor"] = 0
        game["shortcut_help_middle_open"] = False
        game["active"] = False
        game["ui_steering"] = False
        game["free_mode"] = False
        game["paused"] = False

        self._command_buffer = ""
        self._command_cursor = 0
        self._command_history_index = None
        self._command_saved_draft = ""
        nav_index = next((idx for idx, section in enumerate(SECTIONS) if section.id == self.state.section_id), 0)
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                focus=FocusPane.NAVIGATION,
                selected_index=nav_index,
                command_line="",
                status_message="zustand: start",
            )
        )

    def _visual_capabilities(self) -> TerminalVisualCapabilities:
        term = dict(os.environ)
        return TerminalVisualCapabilities(
            ansi=True,
            sixel="sixel" in str(term.get("TERM", "")).lower() or str(term.get("ANANTA_TUI_FORCE_SIXEL", "")).strip() == "1",
            kitty_graphics=bool(str(term.get("KITTY_WINDOW_ID") or "").strip())
            or str(term.get("TERM", "")).lower() == "xterm-kitty",
            opengl_offscreen=str(term.get("ANANTA_TUI_VISUAL_OPENGL", "0")).strip().lower() in {"1", "true", "yes", "on"},
        )

    def _load_visual_viewport_config(self) -> VisualViewportConfig:
        file_mapping: dict[str, Any] = {}
        cfg_path = Path("config/operator_tui_visual_viewport.default.json")
        if cfg_path.exists():
            try:
                parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    file_mapping = dict(parsed.get("visual_viewport") or {})
            except (OSError, json.JSONDecodeError) as exc:
                self._visual_config_error = f"visual config fehler: {exc}"
                file_mapping = {}
        else:
            self._visual_config_error = ""
        env_enabled = str(os.environ.get("ANANTA_TUI_VISUAL_VIEWPORT_ENABLED", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        mapping: dict[str, Any] = {
            **file_mapping,
            "enabled": env_enabled if "ANANTA_TUI_VISUAL_VIEWPORT_ENABLED" in os.environ else bool(file_mapping.get("enabled", False)),
            "default_view": str(
                os.environ.get("ANANTA_TUI_VISUAL_DEFAULT_VIEW", file_mapping.get("default_view", "renderer_diagnostics"))
            ).strip(),
            "default_renderer": str(
                os.environ.get("ANANTA_TUI_VISUAL_DEFAULT_RENDERER", file_mapping.get("default_renderer", "cpu_raster"))
            ).strip(),
            "default_output_adapter": str(
                os.environ.get("ANANTA_TUI_VISUAL_DEFAULT_ADAPTER", file_mapping.get("default_output_adapter", "kitty"))
            ).strip(),
        }
        try:
            cfg = VisualViewportConfig.from_mapping(mapping)
            if not self._visual_config_error:
                self._visual_config_error = ""
            return cfg
        except (TypeError, ValueError) as exc:
            self._visual_config_error = f"visual config fehler: {exc}"
            return VisualViewportConfig()

    def _build_visual_runtime(self) -> VisualRuntime:
        def _build_opengl_renderer():
            from client_surfaces.operator_tui.visual.renderers.opengl_offscreen_renderer import OpenGlOffscreenRenderer

            return OpenGlOffscreenRenderer()

        views = ViewRegistry()
        views.register_factory("logo_animation", lambda: LogoAnimationView())
        views.register_factory("snake_debug_view", lambda: SnakeDebugView())
        views.register_factory("artifact_preview", lambda: ArtifactPreviewView())
        views.register_factory("strategy_map_preview", lambda: StrategyMapPreviewView())
        views.register_factory("renderer_diagnostics", lambda: RendererDiagnosticsView())

        renderers = RendererRegistry()
        renderers.register_factory("ansi_blocks", lambda: AnsiBlocksRenderer())
        renderers.register_factory("cpu_raster", lambda: CpuRasterRenderer())
        renderers.register_factory("svg_raster_optional", lambda: SvgRasterRenderer())
        renderers.register_factory("opengl_offscreen_optional", _build_opengl_renderer)

        adapters = OutputAdapterRegistry()
        adapters.register_factory("ansi", lambda: AnsiOutputAdapter())
        adapters.register_factory("sixel", lambda: SixelOutputAdapter(supported=self._visual_capabilities().sixel))
        adapters.register_factory("kitty", lambda: KittyOutputAdapter(supported=self._visual_capabilities().kitty_graphics))
        adapters.register_factory("noop_diagnostics", lambda: NoopDiagnosticsAdapter())

        return VisualRuntime(
            config=self._visual_viewport_config,
            view_registry=views,
            renderer_registry=renderers,
            adapter_registry=adapters,
            capabilities=self._visual_capabilities(),
        )

    def _ensure_visual_runtime(self) -> VisualRuntime:
        if self._visual_runtime is None:
            self._visual_runtime = self._build_visual_runtime()
        return self._visual_runtime

    def _apply_visual_command_requests(self, state: OperatorState) -> OperatorState:
        game = dict(state.header_logo_game or {})
        requested_view = str(game.get("visual_viewport_active_view_request") or "").strip()
        if not requested_view:
            return state
        runtime = self._ensure_visual_runtime()
        ok = runtime.switch_view(requested_view)
        game.pop("visual_viewport_active_view_request", None)
        game["visual_viewport_enabled"] = True
        game["visual_viewport_active_view"] = requested_view if ok else str(runtime.status().active_view)
        status = f"visual view: {game['visual_viewport_active_view']}" if ok else f"visual view unbekannt: {requested_view}"
        return state.with_updates(header_logo_game=game, status_message=status)

    def _sync_visual_viewport_state(self, *, width: int, height: int) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("visual_viewport_enabled", self._visual_viewport_config.enabled))
        if not enabled:
            game["visual_viewport"] = {"enabled": False}
            if self._visual_config_error:
                game["visual_runtime_status"] = {
                    "runtime_error": self._visual_config_error,
                }
            game.pop("visual_viewport_frame_lines", None)
            self.state = self.state.with_updates(header_logo_game=game)
            return

        runtime = self._ensure_visual_runtime()
        requested_view = str(game.get("visual_viewport_active_view_request") or "").strip()
        if requested_view:
            if runtime.switch_view(requested_view):
                game["visual_viewport_active_view"] = requested_view
            game.pop("visual_viewport_active_view_request", None)

        left_width = 22
        detail_width = 34
        middle_width = max(18, int(width) - left_width - detail_width - 6)
        body_height = max(3, int(height) - 5 - 8)
        body_start = 8
        px_w, px_h = derive_pixel_size(
            columns=middle_width,
            rows=body_height,
            default_pixel_width=self._visual_viewport_config.default_pixel_width,
            default_pixel_height=self._visual_viewport_config.default_pixel_height,
            max_pixel_width=self._visual_viewport_config.max_pixel_width,
            max_pixel_height=self._visual_viewport_config.max_pixel_height,
        )
        region = ViewportRegion(
            x=24,
            y=body_start,
            columns=middle_width,
            rows=body_height,
            pixel_width=px_w,
            pixel_height=px_h,
        )
        state_map = {
            "runtime_status": dict(game.get("visual_runtime_status") or {}),
            "active_view": str(game.get("visual_viewport_active_view") or ""),
            "active_renderer": str(game.get("visual_viewport_active_renderer") or ""),
            "active_adapter": str(game.get("visual_viewport_active_adapter") or ""),
            "artifact": dict(game.get("active_artifact") or {}),
            "allowed_roots": [str(Path.cwd())],
            "snake": list(game.get("snake") or []),
            "target": game.get("food"),
            "territories": list(game.get("territories") or []),
            "selected_territory": game.get("selected_territory"),
            "zoom": game.get("map_zoom", 1.0),
            "selected_heuristic": game.get("selected_heuristic_id"),
            "heuristic_confidence": game.get("heuristic_confidence"),
            "visual_state_version": str(game.get("visual_state_version") or int(time.monotonic())),
            "theme_version": "default",
        }
        frame = runtime.render_frame(region=region, now=time.monotonic(), state=state_map, force=False)
        frame_lines: list[str] = []
        if frame is not None and frame.frame_type == "ansi" and isinstance(frame.payload, list):
            frame_lines = [str(row) for row in frame.payload[:body_height]]
        elif frame is not None:
            frame_lines = [f"[{frame.frame_type}] {frame.mime_or_format} {frame.width}x{frame.height}"]

        status = runtime.status()
        diagnostics = list(status.fallback_diagnostics)
        game["visual_viewport_frame_lines"] = frame_lines
        game["visual_viewport_available_views"] = list(runtime.available_views())
        game["visual_runtime_status"] = {
            "active_view": status.active_view,
            "active_renderer": status.active_renderer,
            "active_adapter": status.active_adapter,
            "rendered_frames": int(status.scheduler.get("rendered_frames", 0)),
            "skipped_frames": int(status.scheduler.get("skipped_frames", 0)),
            "dropped_frames": int(status.scheduler.get("dropped_frames", 0)),
            "fallback_reason": diagnostics[-1] if diagnostics else "",
            "runtime_error": status.runtime_errors[-1] if status.runtime_errors else self._visual_config_error,
        }
        game["visual_viewport"] = {"enabled": True}
        game["visual_viewport_active_view"] = status.active_view
        game["visual_viewport_active_renderer"] = status.active_renderer
        game["visual_viewport_active_adapter"] = status.active_adapter
        self.state = self.state.with_updates(header_logo_game=game)

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
        self._sync_visual_viewport_state(width=size.columns, height=max(18, size.lines - 1))
        return render_operator_shell(self.state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
