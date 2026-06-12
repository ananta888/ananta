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
from client_surfaces.operator_tui.audit_cleanup import run_audit_cleanup_action
from client_surfaces.operator_tui.artifact_intent import ArtifactIntent, ArtifactIntentDetector, IntentConfidence
from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_message,
    configure_middle_view_for_history_entry,
    is_showing_chat_long_message,
    latest_long_message_for_channel,
    long_message_history_rows,
    refresh_rendered_view,
    toggle_render_mode,
)
from client_surfaces.operator_tui.mouse import (
    MouseEventType as NormalizedMouseEventType,
    MouseState,
    detect_mouse_support,
    normalize_mouse_state,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.keybindings_config import key_for_action, keybinding_conflicts
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
from client_surfaces.operator_tui.tui_snapshot import rendered_tui_snapshot_text, write_tui_snapshot

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
from client_surfaces.operator_tui.visual.views.markdown_mermaid_document_view import MarkdownMermaidDocumentView
from client_surfaces.operator_tui.visual.views.renderer_diagnostics_view import RendererDiagnosticsView
from client_surfaces.operator_tui.visual.views.snake_debug_view import SnakeDebugView
from client_surfaces.operator_tui.visual.views.strategy_map_preview_view import StrategyMapPreviewView
from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.backends.wslg_webview_backend import WslgWebviewBackend
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState
from client_surfaces.operator_tui.windowing.view_models.ai_snake_window_model import build_ai_snake_window_model
from client_surfaces.operator_tui.windowing.view_models.center_window_model import build_center_window_model

from client_surfaces.operator_tui import _interactive_keybindings as _ik
from client_surfaces.operator_tui import _interactive_audit as _ia
from client_surfaces.operator_tui import _interactive_template as _it
from client_surfaces.operator_tui import _interactive_command as _ic
from client_surfaces.operator_tui import _interactive_ai_config as _iconfig
from client_surfaces.operator_tui import _interactive_visual as _iv
from client_surfaces.operator_tui import _interactive_window as _iw
from client_surfaces.operator_tui import _interactive_chat as _ichat

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
        created_default_header_game = False
        if self._header_snake_enabled() and not self.state.header_logo_game:
            self.state = self.state.with_updates(header_logo_game=self._default_header_snake())
            created_default_header_game = True
        if created_default_header_game:
            game = dict(self.state.header_logo_game or {})
            game["active"] = False
            game["ui_steering"] = False
            game["free_mode"] = False
            game["ai_snake_config_open"] = False
            game["ai_snake_config_combo"] = {"open": False}
            try:
                from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

                chat = get_chat_state(game)
                chat["chat_focus"] = False
                chat["chat_input_history_index"] = None
                set_chat_state(game, chat)
            except Exception:
                pass
            self.state = self.state.with_updates(header_logo_game=game)
        self._restore_oidc_token()
        if not self.state.open_tabs:
            from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_section
            self.state = open_or_activate_tab(
                self.state, section_id=self.state.section_id, kind="section",
                label=tab_label_for_section(self.state.section_id),
            )
        self._keybinding_conflicts = keybinding_conflicts()
        if self._keybinding_conflicts:
            game = dict(self.state.header_logo_game or {})
            game["keybinding_conflicts"] = list(self._keybinding_conflicts)
            first = dict(self._keybinding_conflicts[0])
            key = str(first.get("key") or "?")
            actions = ", ".join(str(item) for item in (first.get("actions") or []))
            self.state = self.state.with_updates(
                header_logo_game=game,
                status_message=f"keybinding-konflikt: {key} -> {actions}",
            )
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
        self._last_command_feedback: str = ""
        self._last_command_feedback_at: float = 0.0
        self._browser_controller: object | None = None
        self._external_window_controller: ExternalWindowController | None = None
        self._command_history: list[str] = []
        self._command_history_index: int | None = None
        self._command_saved_draft = ""
        # Load persisted input histories from user.json
        self._load_input_histories()
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
            self._tick_center_browser()
            self._tick_external_window()
            self._rendered_text = self._render()
            self._app.invalidate()
            await asyncio.sleep(delay)

    def _ensure_external_window_controller(self) -> ExternalWindowController:
        return _iw.ensure_external_window_controller(self)

    def _tick_center_browser(self) -> None:
        _iw.tick_center_browser(self)

    def _tick_external_window(self) -> None:
        _iw.tick_external_window(self)

    def _build_external_window_state_payload(self) -> dict[str, Any]:
        return _iw.build_external_window_state_payload(self)

    def _apply_external_window_action(self, action_id: str, args: dict[str, Any] | None = None) -> None:
        _iw.apply_external_window_action(self, action_id, args)

    def _apply_settings_from_browser(self) -> None:
        _iw.apply_settings_from_browser(self)

    def _build_auth_context_for_window(self) -> dict[str, str]:
        return _iw._build_auth_context_for_window(self)

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
        return _ik.build_keybindings(self)

    # ── Chat focus helpers (E01.04) ───────────────────────────────────────────

    def _chat_focus_active(self) -> bool:
        return _ichat.chat_focus_active(self)

    def _chat_panel_available(self) -> bool:
        return _ichat.chat_panel_available(self)

    def _artifact_chat_focus_active(self) -> bool:
        return _ichat.artifact_chat_focus_active(self)

    def _get_scroll_manager(self):
        return _ichat.get_scroll_manager(self)

    def _get_focus_manager(self):
        return _ichat.get_focus_manager(self)

    def _sync_scroll_focus_and_mouse_regions(
        self,
        *,
        width: int,
        height: int,
        content_width: int,
        body_start: int,
        body_height: int,
    ) -> None:
        _ichat.sync_scroll_focus_and_mouse_regions(
            self, width=width, height=height, content_width=content_width,
            body_start=body_start, body_height=body_height,
        )

    def _scroll_active_panel(self, direction: str) -> None:
        _ichat.scroll_active_panel(self, direction)

    def _h_scroll_center(self, delta: int) -> None:
        _ichat.h_scroll_center(self, delta)

    def _toggle_visual_view_switcher_overlay(self) -> None:
        _ichat.toggle_visual_view_switcher_overlay(self)

    def _next_visual_view(self) -> None:
        _ichat.next_visual_view(self)

    def _previous_visual_view(self) -> None:
        _ichat.previous_visual_view(self)

    def _toggle_chat_panel_open(self) -> None:
        _ichat.toggle_chat_panel_open(self)

    def _toggle_context_help(self) -> None:
        _ichat.toggle_context_help(self)

    def _send_terminal_context_to_ai(self) -> None:
        _ichat.send_terminal_context_to_ai(self)

    def _chat_cycle_channel(self) -> None:
        _ichat.chat_cycle_channel(self)

    def _chat_switch_channel(self, channel_id: str) -> None:
        _ichat.chat_switch_channel(self, channel_id)

    def _chat_focus_enter(self) -> None:
        _ichat.chat_focus_enter(self)

    def _chat_focus_leave(self) -> None:
        _ichat.chat_focus_leave(self)

    def _toggle_chat_focus(self) -> None:
        _ichat.toggle_chat_focus(self)

    def _chat_append(self, ch: str) -> None:
        _ichat.chat_append(self, ch)

    def _chat_backspace(self) -> None:
        _ichat.chat_backspace(self)

    def _chat_delete(self) -> None:
        _ichat.chat_delete(self)

    def _chat_move_cursor(self, delta: int) -> None:
        _ichat.chat_move_cursor(self, delta)

    def _chat_history_move(self, step: int) -> None:
        _ichat.chat_history_move(self, step)

    def _chat_clear_input(self) -> None:
        _ichat.chat_clear_input(self)

    def _artifact_chat_focus_enter(self) -> None:
        _ichat.artifact_chat_focus_enter(self)

    def _artifact_chat_focus_leave(self, *, clear: bool = False) -> None:
        _ichat.artifact_chat_focus_leave(self, clear=clear)

    def _artifact_chat_append(self, ch: str) -> None:
        _ichat.artifact_chat_append(self, ch)

    def _artifact_chat_backspace(self) -> None:
        _ichat.artifact_chat_backspace(self)

    def _artifact_chat_delete(self) -> None:
        _ichat.artifact_chat_delete(self)

    def _artifact_chat_move_cursor(self, delta: int) -> None:
        _ichat.artifact_chat_move_cursor(self, delta)

    def _artifact_chat_history_move(self, step: int) -> None:
        _ichat.artifact_chat_history_move(self, step)

    def _artifact_chat_clear_input(self) -> None:
        _ichat.artifact_chat_clear_input(self)

    def _artifact_chat_send_message(self) -> None:
        _ichat.artifact_chat_send_message(self)

    def _chat_scroll(self, delta: int) -> None:
        _ichat.chat_scroll(self, delta)

    def _copy_chat_panel_snapshot(self) -> None:
        _ichat.copy_chat_panel_snapshot(self)

    def _copy_ai_status_snapshot(self) -> None:
        _ichat.copy_ai_status_snapshot(self)

    def _current_rendered_text(self) -> str:
        return _ichat.current_rendered_text(self)

    def _copy_tui_snapshot(self) -> None:
        _ichat.copy_tui_snapshot(self)

    def _save_tui_snapshot(self) -> None:
        _ichat.save_tui_snapshot(self)

    def _open_latest_long_chat_message(self) -> None:
        _ichat.open_latest_long_chat_message(self)

    def _normal_or_text(self, text: str, normal_action) -> None:
        _ic.normal_or_text(self, text, normal_action)

    def _audit_viewer_active(self) -> bool:
        return _ia.audit_viewer_active(self)

    def _audit_cleanup_confirm_mode_active(self) -> bool:
        return _ia.audit_cleanup_confirm_mode_active(self)

    def _audit_cleanup_result_mode_active(self) -> bool:
        return _ia.audit_cleanup_result_mode_active(self)

    def _audit_cleanup_set_choice(self, choice: str) -> None:
        _ia.audit_cleanup_set_choice(self, choice)

    def _audit_cleanup_close_viewer(self, *, status_message: str) -> None:
        _ia.audit_cleanup_close_viewer(self, status_message=status_message)

    def _audit_cleanup_show_result(self, *, title: str, summary: str) -> None:
        _ia.audit_cleanup_show_result(self, title=title, summary=summary)

    def _audit_cleanup_button_choice_from_click(self, *, x: int, y: int, width: int, height: int) -> str | None:
        return _ia.audit_cleanup_button_choice_from_click(self, x=x, y=y, width=width, height=height)

    def _audit_cleanup_handle_mouse_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        return _ia.audit_cleanup_handle_mouse_click(self, x=x, y=y, width=width, height=height)

    def _selected_audit_entry(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        return _ia.selected_audit_entry(self)

    def _audit_viewer_viewport_metrics(self) -> tuple[int, int]:
        return _ia.audit_viewer_viewport_metrics(self)

    def _audit_viewer_scroll_vertical(self, delta_lines: int) -> None:
        _ia.audit_viewer_scroll_vertical(self, delta_lines)

    def _audit_viewer_scroll_horizontal(self, delta_cols: int) -> None:
        _ia.audit_viewer_scroll_horizontal(self, delta_cols)

    def _open_audit_viewer_for_selected(self) -> bool:
        return _ia.open_audit_viewer_for_selected(self)

    def _clear_runtime_chat_history(self, game: dict[str, Any]) -> None:
        _ia.clear_runtime_chat_history(self, game)

    def _clear_persisted_chat_history(self) -> None:
        _ia.clear_persisted_chat_history(self)

    def _confirm_audit_cleanup_action(self) -> bool:
        return _ia.confirm_audit_cleanup_action(self)

    def _template_editor_active(self) -> bool:
        return _it.template_editor_active(self)

    def _selected_template_entry(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        return _it.selected_template_entry(self)

    def _template_editor_text_for_item(self, *, kind: str, item: dict[str, Any], raw: dict[str, Any]) -> str:
        return _it.template_editor_text_for_item(self, kind=kind, item=item, raw=raw)

    def _template_editor_viewport_metrics(self) -> tuple[int, int]:
        return _it.template_editor_viewport_metrics(self)

    def _template_editor_ensure_cursor_visible(self, editor: dict[str, Any]) -> dict[str, Any]:
        return _it.template_editor_ensure_cursor_visible(self, editor)

    def _template_editor_scroll_vertical(self, delta_lines: int) -> None:
        _it.template_editor_scroll_vertical(self, delta_lines)

    def _template_editor_set_cursor_from_content_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        return _it.template_editor_set_cursor_from_content_click(self, x=x, y=y, width=width, height=height)

    def _open_template_editor_for_selected(self) -> bool:
        return _it.open_template_editor_for_selected(self)

    def _template_editor_insert_text(self, text: str) -> None:
        _it.template_editor_insert_text(self, text)

    def _template_editor_backspace(self) -> None:
        _it.template_editor_backspace(self)

    def _template_editor_delete(self) -> None:
        _it.template_editor_delete(self)

    def _template_editor_move_cursor(self, delta: int) -> None:
        _it.template_editor_move_cursor(self, delta)

    def _template_editor_move_cursor_vertical(self, direction: int) -> None:
        _it.template_editor_move_cursor_vertical(self, direction)

    def _template_editor_save(self) -> None:
        _it.template_editor_save(self)

    def _handle_enter_key(self) -> None:
        _ic.handle_enter_key(self)

    def _cancel_active_input_mode(self) -> bool:
        return _ic.cancel_active_input_mode(self)

    def _append_command(self, text: str) -> None:
        _ic.append_command(self, text)

    def _command_backspace(self) -> None:
        _ic.command_backspace(self)

    def _command_delete(self) -> None:
        _ic.command_delete(self)

    def _command_move_cursor(self, delta: int) -> None:
        _ic.command_move_cursor(self, delta)

    def _command_history_move(self, delta: int) -> None:
        _ic.command_history_move(self, delta)

    def _input_history_config(self) -> dict[str, Any]:
        return _ic.input_history_config(self)

    def _apply_input_history_to_game(self, game: dict[str, Any]) -> None:
        _ic.apply_input_history_to_game(self, game)

    def _save_chat_to_history(self, text: str) -> None:
        _ic.save_chat_to_history(self, text)

    def _load_input_histories(self) -> None:
        _ic.load_input_histories(self)

    def _save_command_to_history(self, text: str) -> None:
        _ic.save_command_to_history(self, text)

    def _command_commit_history(self) -> None:
        _ic.command_commit_history(self)

    def _command_reset(self) -> None:
        _ic.command_reset(self)

    def _open_command_mode(self) -> None:
        _ic.open_command_mode(self)

    def _exit_command_mode_for_global_shortcut(self) -> None:
        _ic.exit_command_mode_for_global_shortcut(self)

    def _enter_command_mode_from_anywhere(self) -> None:
        _ic.enter_command_mode_from_anywhere(self)

    def _sync_command_line_state(self) -> None:
        _ic.sync_command_line_state(self)

    def _toggle_ai_snake_config_panel(self) -> None:
        _iconfig.toggle_ai_snake_config_panel(self)

    def _toggle_ai_snake_config_selected(self) -> None:
        _iconfig.toggle_ai_snake_config_selected(self)

    def _ai_snake_config_combo_active(self, game: dict[str, object] | None = None) -> bool:
        return _iconfig.ai_snake_config_combo_active(self, game)

    def _ai_snake_config_next_index(self, delta: int, game: dict[str, object] | None = None) -> int:
        return _iconfig.ai_snake_config_next_index(self, delta, game)

    def _open_ai_snake_config_combo(self, game: dict[str, object], *, key: str, idx: int) -> None:
        _iconfig.open_ai_snake_config_combo(self, game, key=key, idx=idx)

    def _ai_snake_config_combo_close(self, *, status: str = "ai config: auswahl geschlossen") -> None:
        _iconfig.ai_snake_config_combo_close(self, status=status)

    def _ai_snake_config_combo_filter_text(self, combo: dict[str, object]) -> str:
        return _iconfig.ai_snake_config_combo_filter_text(self, combo)

    def _ai_snake_config_combo_apply(self, game: dict[str, object], *, value: str) -> None:
        _iconfig.ai_snake_config_combo_apply(self, game, value=value)

    def _ai_snake_config_combo_commit(self) -> None:
        _iconfig.ai_snake_config_combo_commit(self)

    def _ai_snake_config_combo_move(self, delta: int) -> None:
        _iconfig.ai_snake_config_combo_move(self, delta)

    def _ai_snake_config_combo_append_filter(self, ch: str) -> None:
        _iconfig.ai_snake_config_combo_append_filter(self, ch)

    def _ai_snake_config_combo_backspace(self) -> None:
        _iconfig.ai_snake_config_combo_backspace(self)

    def _ai_snake_config_combo_delete(self) -> None:
        _iconfig.ai_snake_config_combo_delete(self)

    def _ai_snake_config_combo_move_cursor(self, delta: int) -> None:
        _iconfig.ai_snake_config_combo_move_cursor(self, delta)

    def _ai_snake_config_combo_select_value(self, *, value: str) -> None:
        _iconfig.ai_snake_config_combo_select_value(self, value=value)

    def _handle_quit_key(self, event) -> None:
        if self._external_window_controller is not None:
            try:
                self._external_window_controller.close()
            except Exception:
                pass
        self._flush_config_on_exit()
        event.app.exit()

    def _flush_config_on_exit(self) -> None:
        try:
            from client_surfaces.operator_tui.config.user_config_manager import flush_user_config
            game = dict(self.state.header_logo_game or {})
            flush_user_config(game)
        except Exception:
            pass

    def _toggle_snake_mouse_follow(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("mouse_follow_enabled"))
        game["mouse_follow_enabled"] = not enabled
        game["movement_mode"] = "mouse_follow" if not enabled else "keyboard"
        status = "an" if not enabled else "aus"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake mouse-follow: {status}"))

    def _escape_to_start_state(self) -> None:
        if self._audit_viewer_active():
            game = dict(self.state.header_logo_game or {})
            game["audit_viewer"] = {"active": False}
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    focus=FocusPane.CONTENT,
                    status_message="audit viewer: geschlossen",
                )
            )
            return
        if self._template_editor_active():
            game = dict(self.state.header_logo_game or {})
            game["template_editor"] = {"active": False}
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    focus=FocusPane.CONTENT,
                    status_message="template editor: geschlossen",
                )
            )
            return
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
        game["center_browser_active"] = False
        game["center_browser_status"] = "exited"
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
        return _iv.visual_capabilities(self)

    def _load_visual_viewport_config(self) -> VisualViewportConfig:
        return _iv.load_visual_viewport_config(self)

    def _build_visual_runtime(self) -> VisualRuntime:
        return _iv.build_visual_runtime(self)

    def _ensure_visual_runtime(self) -> VisualRuntime:
        return _iv.ensure_visual_runtime(self)

    def _apply_visual_command_requests(self, state: OperatorState) -> OperatorState:
        return _iv.apply_visual_command_requests(self, state)

    def _sync_visual_viewport_state(self, *, width: int, height: int) -> None:
        _iv.sync_visual_viewport_state(self, width=width, height=height)

    def _tab_close_active(self) -> None:
        if not self.state.active_tab_id:
            return
        from client_surfaces.operator_tui.tab_manager import close_tab
        new_state = close_tab(self.state, self.state.active_tab_id)
        game = dict(new_state.header_logo_game or {})
        game["visual_viewport_enabled"] = False
        game["visual_viewport"] = {"enabled": False}
        self._set_state(new_state.with_updates(header_logo_game=game))

    def _tab_cycle(self, delta: int) -> None:
        tabs = self.state.open_tabs
        if not tabs:
            return
        from client_surfaces.operator_tui.tab_manager import activate_tab
        cur_idx = next((i for i, t in enumerate(tabs) if t.id == self.state.active_tab_id), 0)
        new_idx = (cur_idx + delta) % len(tabs)
        new_state, new_game = activate_tab(
            self.state, tabs[new_idx].id,
            game=dict(self.state.header_logo_game or {}),
        )
        if new_state.section_id != self.state.section_id:
            from client_surfaces.operator_tui.app import load_active_section
            new_state = load_active_section(new_state, self._registry)
        self._set_state(new_state.with_updates(header_logo_game=new_game))

    def _restore_oidc_token(self) -> None:
        """Lädt gecachten OIDC-Token beim Start, wenn noch nicht abgelaufen."""
        from client_surfaces.operator_tui.network_profile import rendezvous_base_url
        from client_surfaces.operator_tui.hub_loader import set_share_oidc_token
        env_token = str(
            os.environ.get("ANANTA_TUI_E2E_OIDC_TOKEN")
            or os.environ.get("ANANTA_TUI_OIDC_TOKEN")
            or ""
        ).strip()
        game = dict(self.state.header_logo_game or {})
        if game.get("oidc_token"):
            return  # bereits gesetzt
        if env_token:
            game["oidc_token"] = env_token
            set_share_oidc_token(env_token, rendezvous_base_url())
            game["oidc_device_flow"] = {"status": "done", "user_code": "", "verification_uri": "", "error": ""}
            self.state = self.state.with_updates(
                header_logo_game=game,
                status_message="OIDC: Session aus Environment geladen",
            )
            return

        from client_surfaces.operator_tui.snake_persistence import load_oidc_token
        cached = load_oidc_token()
        if not cached:
            return
        token = str(cached.get("access_token") or "")
        if not token:
            return
        game["oidc_token"] = token
        set_share_oidc_token(token, rendezvous_base_url())
        issuer = str(cached.get("issuer") or "")
        username = str(cached.get("username") or "")
        game["oidc_device_flow"] = {"status": "done", "user_code": "", "verification_uri": "", "error": ""}
        self.state = self.state.with_updates(
            header_logo_game=game,
            status_message=f"OIDC: Session wiederhergestellt{' – ' + username if username else ''}",
        )

    def _set_state(self, state: OperatorState) -> None:
        if self._splash is not None:
            from agent.cli.splash import SplashState
            ctx = self._splash.context
            if ctx.state in (SplashState.FULLSCREEN, SplashState.TRANSITION):
                self._splash.transition_to(SplashState.COMPACT_HEADER)
        if state.section_id != self.state.section_id:
            game = dict(state.header_logo_game or {})
            game["visual_viewport_enabled"] = False
            game["visual_viewport"] = {"enabled": False}
            state = state.with_updates(header_logo_game=game)
        self.state = state
        self._rendered_text = self._render()
        self._app.invalidate()

    def _render(self) -> str:
        if self._splash is not None:
            self._splash.tick()
        # Tick browser even when 3D header loop is disabled
        game = self.state.header_logo_game or {}
        if bool(game.get("center_browser_active")) and not self._header_3d_active():
            self._tick_center_browser()
        if bool(game.get("center_window_active")) and not self._header_3d_active():
            self._tick_external_window()
        size = shutil.get_terminal_size((120, 32))
        self._sync_visual_viewport_state(width=size.columns, height=max(18, size.lines - 1))
        return render_operator_shell(self.state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
