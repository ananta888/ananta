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
        if self._external_window_controller is None:
            self._external_window_controller = ExternalWindowController(
                surface=WslgWebviewBackend(),
                bridge=ExternalWindowBridgeServer(),
            )
        return self._external_window_controller

    def _tick_center_browser(self) -> None:
        """Drive the BrowserModeController each frame when browser mode is active."""
        game = dict(self.state.header_logo_game or {})
        if not bool(game.get("center_browser_active")):
            # Browser deactivated — stop controller if running
            if self._browser_controller is not None:
                try:
                    self._browser_controller.exit_browser_mode()  # type: ignore[union-attr]
                except Exception:
                    pass
                self._browser_controller = None
            return

        url = str(game.get("center_browser_url") or "")
        status = str(game.get("center_browser_status") or "")

        # First activation: start the controller
        if self._browser_controller is None and status == "requested":
            try:
                from client_surfaces.operator_tui.visual.browser.browser_mode_controller import BrowserModeController
                import shutil as _sh
                size = _sh.get_terminal_size((120, 32))
                wide_browser_layout = bool(game.get("center_browser_wide_layout")) or (
                    str(os.environ.get("ANANTA_TUI_BROWSER_WIDE_LAYOUT") or "").strip().lower()
                    in {"1", "true", "yes", "on"}
                )
                if wide_browser_layout:
                    left_w, detail_w = ((12, 18) if size.columns >= 100 else (10, 14))
                else:
                    left_w, detail_w = (22, 34)
                center_w = max(20, size.columns - left_w - detail_w - 6)
                body_h = max(8, size.lines - 8)
                ctrl = BrowserModeController()
                if url:
                    ctrl.enter_url(url, cols=center_w, rows=body_h, allow_remote=True)
                else:
                    from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot
                    snap = CenterContentSnapshot(
                        content_type="plain_text", title="Browser",
                        source_text="(kein Inhalt)", html_text="", metadata={},
                        scroll_position=0, unsupported_reason="",
                    )
                    ctrl.enter_browser_mode(snap, cols=center_w, rows=body_h)
                self._browser_controller = ctrl
                game["center_browser_status"] = "active"
                game["center_browser_error"] = str(ctrl.error_message) if hasattr(ctrl, "error_message") else ""
                self._set_state(self.state.with_updates(header_logo_game=game))
            except Exception as exc:
                game["center_browser_active"] = False
                game["center_browser_status"] = "error"
                game["center_browser_error"] = str(exc)
                game["_cmd_feedback"] = f"browser error: {exc}"
                import time as _t
                game["_cmd_feedback_at"] = _t.monotonic()
                self._browser_controller = None
                self._set_state(self.state.with_updates(header_logo_game=game))
            return

        # Running: read output and store in game for content renderer
        if self._browser_controller is not None:
            try:
                chunk = self._browser_controller.tick()  # type: ignore[union-attr]
                if chunk:
                    existing = bytes(game.get("_browser_frame_bytes") or b"")
                    # Keep last 64 KB of output for rendering
                    combined = (existing + chunk)[-65536:]
                    game["_browser_frame_bytes"] = combined
                    self._set_state(self.state.with_updates(header_logo_game=game))
                # Check if controller exited unexpectedly
                if not self._browser_controller.is_running():  # type: ignore[union-attr]
                    game["center_browser_active"] = False
                    game["center_browser_status"] = "stopped"
                    game["_cmd_feedback"] = "browser: beendet"
                    import time as _t
                    game["_cmd_feedback_at"] = _t.monotonic()
                    self._browser_controller = None
                    self._set_state(self.state.with_updates(header_logo_game=game))
            except Exception as exc:
                game["center_browser_active"] = False
                game["center_browser_status"] = "error"
                game["_cmd_feedback"] = f"browser error: {exc}"
                import time as _t
                game["_cmd_feedback_at"] = _t.monotonic()
                self._browser_controller = None
                self._set_state(self.state.with_updates(header_logo_game=game))

    def _tick_external_window(self) -> None:
        game = dict(self.state.header_logo_game or {})
        command = str(game.pop("center_window_command", "")).strip().lower()
        requested_view_mode = str(game.pop("center_window_view_mode_request", "")).strip().lower()
        if command:
            ctrl = self._ensure_external_window_controller()
            if command == "center.window.open":
                st = ctrl.open()
                game["center_window_url"] = ctrl.view_url()
            elif command == "center.window.close":
                st = ctrl.close()
            elif command == "center.window.restart":
                st = ctrl.restart()
                game["center_window_url"] = ctrl.view_url()
            else:
                st = ctrl.status()
            game["center_window_state"] = st.state.value
            game["center_window_backend"] = st.backend
            game["center_window_bridge_port"] = st.bridge_port
            game["center_window_reason"] = st.reason
            game["center_window_active"] = st.state in {ExternalWindowState.ACTIVE, ExternalWindowState.STARTING}
            game["center_window_view_mode"] = str(game.get("center_window_view_mode") or "simple")
            game["center_window_reason_code"] = (
                "window_ok" if st.state in {ExternalWindowState.ACTIVE, ExternalWindowState.STARTING} else (
                    "window_degraded" if st.state == ExternalWindowState.DEGRADED else (
                        "window_failed" if st.state == ExternalWindowState.FAILED else "window_inactive"
                    )
                )
            )
            msg = (
                f"center window: {st.state.value} backend={st.backend} bridge={st.bridge_host}:{st.bridge_port}"
                f" dropped={st.dropped_events} rejected={st.rejected_actions} accepted={st.accepted_actions}"
                f" reason_code={game.get('center_window_reason_code')}"
                + (f" reason={st.reason}" if st.reason else "")
            )
            import time as _t
            game["_cmd_feedback"] = msg
            game["_cmd_feedback_at"] = _t.monotonic()
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=msg))

        if requested_view_mode in {"simple", "doc", "snake"}:
            self._apply_external_window_action(f"view.{requested_view_mode}")

        ctrl = self._external_window_controller
        if ctrl is None:
            return
        st_now = ctrl.status()
        game = dict(self.state.header_logo_game or {})
        game["center_window_state"] = st_now.state.value
        game["center_window_backend"] = st_now.backend
        game["center_window_bridge_port"] = st_now.bridge_port
        game["center_window_bridge_connected"] = bool(st_now.bridge_running)
        game["center_window_dropped_events"] = int(st_now.dropped_events)
        game["center_window_rejected_actions"] = int(st_now.rejected_actions)
        game["center_window_accepted_actions"] = int(st_now.accepted_actions)
        game["center_window_reason"] = st_now.reason
        game["center_window_active"] = st_now.state in {ExternalWindowState.ACTIVE, ExternalWindowState.STARTING}
        self.state = self.state.with_updates(header_logo_game=game)
        ctrl.publish_state(self._build_external_window_state_payload())
        for event in ctrl.drain_events():
            self._apply_external_window_action(str(getattr(event, "action_id", "")))

    def _build_external_window_state_payload(self) -> dict[str, Any]:
        game = dict(self.state.header_logo_game or {})
        center_model = build_center_window_model(state=self.state, game=game)
        snake_model = build_ai_snake_window_model(game)
        return {
            "state_version": str(int(time.monotonic() * 1000)),
            "mode": center_model["mode"],
            "section": center_model["section"],
            "focus": center_model["focus"],
            "status_message": center_model["status_message"],
            "visual_view": center_model["visual_view"],
            "center_browser_active": center_model["center_browser_active"],
            "center_window_active": bool(game.get("center_window_active")),
            "snake": snake_model,
        }

    def _apply_external_window_action(self, action_id: str) -> None:
        aid = str(action_id or "").strip()
        if not aid:
            return
        if aid == "view.simple":
            game = dict(self.state.header_logo_game or {})
            game["center_window_view_mode"] = "simple"
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="window action: view simple"))
            return
        if aid == "view.doc":
            game = dict(self.state.header_logo_game or {})
            game["center_window_view_mode"] = "doc"
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="window action: view doc"))
            self._run_command(":doc switch")
            return
        if aid == "view.snake":
            game = dict(self.state.header_logo_game or {})
            game["center_window_view_mode"] = "snake"
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="window action: view snake"))
            if not bool(game.get("snake_mode")):
                self._toggle_snake_mode()
            return
        if aid == "view.next":
            self._next_visual_view()
            return
        if aid == "view.previous":
            self._previous_visual_view()
            return
        if aid == "focus.center":
            self._set_state(self.state.with_updates(focus=FocusPane.CONTENT, status_message="window action: focus center"))
            return
        if aid == "focus.nav":
            self._set_state(self.state.with_updates(focus=FocusPane.NAVIGATION, status_message="window action: focus nav"))
            return
        game = dict(self.state.header_logo_game or {})
        if aid == "snake.pause":
            if bool(game.get("snake_mode")) and not bool(game.get("paused")):
                self._toggle_snake_pause()
            return
        if aid == "snake.resume":
            if bool(game.get("snake_mode")) and bool(game.get("paused")):
                self._toggle_snake_pause()
            return

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
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(":")
                return
            if self._snake_message_mode_active():
                self._snake_message_append(":")
                return
            if self._snake_mode_active():
                self._enter_command_mode_from_anywhere()
                return
            # Snake mode does NOT block `:` — commands must remain reachable at all times.
            if self._chat_focus_active():
                self._chat_append(":")
                return
            self._open_command_mode()

        @bindings.add("/")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append("/")
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("/")
                return
            self._enter_command_mode_from_anywhere()

        @bindings.add("enter")
        @bindings.add("c-m")
        @bindings.add("c-j")
        def _(event) -> None:
            self._handle_enter_key()

        @bindings.add("escape")
        def _(event) -> None:
            self._escape_to_start_state()

        @bindings.add("backspace")
        @bindings.add("c-h")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_backspace()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_backspace()
                return
            if self._chat_focus_active():
                self._chat_backspace()
                return
            if self._audit_viewer_active():
                return
            if self._template_editor_active():
                self._template_editor_backspace()
                return
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_backspace()
                return
            if self._snake_message_mode_active():
                self._snake_message_backspace()
                return
            if self._snake_mode_active():
                return

        @bindings.add("delete")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_delete()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_delete()
                return
            if self._chat_focus_active():
                self._chat_delete()
                return
            if self._audit_viewer_active():
                return
            if self._template_editor_active():
                self._template_editor_delete()
                return
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_delete()
                return
            if self._snake_message_mode_active():
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
                self._set_selected_index(self._clamp_down())
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
            self._normal_or_text("k", lambda: self._set_selected_index(max(0, self.state.selected_index - 1)))

        @bindings.add(key_for_action("inspect", "c-f"))
        def _(event) -> None:
            def _e():
                if self.state.section_id == "templates" and self.state.focus is FocusPane.CONTENT:
                    if self._open_template_editor_for_selected():
                        return
                if self.state.section_id == "audit" and self.state.focus is FocusPane.CONTENT:
                    if self._open_audit_viewer_for_selected():
                        return
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
            game = dict(self.state.header_logo_game or {})
            if is_showing_chat_long_message(game):
                refresh_rendered_view(game)
                self._set_state(self.state.with_updates(header_logo_game=game, status_message="Chat-Ansicht: Render aktualisiert"))
                return
            self._normal_or_text("r", lambda: self._run_command(":refresh"))

        @bindings.add(key_for_action("help", "c-y"))
        def _(event) -> None:
            self._normal_or_text("?", lambda: self._run_command(":help"))

        @bindings.add(key_for_action("cycle_focus_or_channel", "c-w"))
        def _(event) -> None:
            if self._chat_focus_active() or self._artifact_chat_focus_active() or self._snake_mode_active():
                self._chat_cycle_channel()
                return
            if self.state.open_tabs and self.state.mode is OperatorMode.NORMAL:
                self._tab_close_active()
                return
            self._exit_command_mode_for_global_shortcut()
            self._move_focus(1)

        @bindings.add(key_for_action("tab_next", "c-right"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._tab_cycle(1)

        @bindings.add(key_for_action("tab_prev", "c-left"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._tab_cycle(-1)

        @bindings.add(key_for_action("snake_pause", "c-p"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_pause()  # T01.02: Space togglet Pause statt Stopp

        @bindings.add(key_for_action("toggle_snake_mode", "c-s"))
        def _(event) -> None:
            if self._template_editor_active():
                self._template_editor_save()
                return
            self._exit_command_mode_for_global_shortcut()
            self._toggle_snake_mode()

        @bindings.add(key_for_action("chat_focus", "c-e"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._toggle_chat_focus()

        @bindings.add(key_for_action("toggle_chat_panel", "c-g"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._toggle_chat_panel_open()

        @bindings.add(key_for_action("copy_chat_panel", "c-c"))
        def _(event) -> None:
            if self._snake_mode_active():
                self._snake_copy_selection()
                return
            self._copy_chat_panel_snapshot()

        @bindings.add(key_for_action("copy_tui_snapshot", "c-\\"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._copy_tui_snapshot()

        @bindings.add(key_for_action("save_tui_snapshot", "c-_"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._save_tui_snapshot()

        @bindings.add(key_for_action("clear_chat_input", "c-l"))
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_clear_input()
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_clear_input()

        @bindings.add(key_for_action("open_long_chat_message", "c-space"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._open_latest_long_chat_message()

        @bindings.add(key_for_action("toggle_visual_view_switcher_overlay", "f8"))
        def _(event) -> None:
            self._toggle_visual_view_switcher_overlay()

        @bindings.add(key_for_action("center_browser_toggle", "f5"))
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            result = execute_command("center.browser.toggle", self.state)
            self._set_state(result.state)

        @bindings.add(key_for_action("open_center_webview", "c-0"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._run_command(":center.webview.open")

        @bindings.add(key_for_action("open_center_window", "c-9"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._run_command(":center.window.open")

        @bindings.add(key_for_action("switch_center_to_doc_view", "f6"))
        def _(event) -> None:
            self._exit_command_mode_for_global_shortcut()
            self._run_command(":doc switch")

        @bindings.add(key_for_action("next_visual_view", "f9"))
        def _(event) -> None:
            self._next_visual_view()

        @bindings.add(key_for_action("previous_visual_view", "f10"))
        def _(event) -> None:
            self._previous_visual_view()

        @bindings.add(key_for_action("toggle_ai_snake_config", "f6"))
        def _(event) -> None:
            self._toggle_ai_snake_config_panel()

        @bindings.add("left")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_move_cursor(-1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_move_cursor(-1)
                return
            if self._chat_focus_active():
                self._chat_move_cursor(-1)
                return
            if self._audit_viewer_active():
                if self._audit_cleanup_confirm_mode_active():
                    self._audit_cleanup_set_choice("delete")
                    return
                self._audit_viewer_scroll_horizontal(-4)
                return
            if self._template_editor_active():
                self._template_editor_move_cursor(-1)
                return
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_move_cursor(-1)
                return
            if self._try_header_snake_direction((-1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("right")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_move_cursor(1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_move_cursor(1)
                return
            if self._chat_focus_active():
                self._chat_move_cursor(1)
                return
            if self._audit_viewer_active():
                if self._audit_cleanup_confirm_mode_active():
                    self._audit_cleanup_set_choice("cancel")
                    return
                self._audit_viewer_scroll_horizontal(4)
                return
            if self._template_editor_active():
                self._template_editor_move_cursor(1)
                return
            if self._ai_snake_config_combo_active(game):
                self._ai_snake_config_combo_move_cursor(1)
                return
            if self._try_header_snake_direction((1, 0)):
                return
            self._set_selected_index(self._clamp_down())

        @bindings.add("up")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_history_move(-1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_history_move(-1)
                return
            if self._chat_focus_active():
                self._chat_history_move(-1)
                return
            if self._audit_viewer_active():
                self._audit_viewer_scroll_vertical(-1)
                return
            if self._template_editor_active():
                self._template_editor_move_cursor_vertical(-1)
                return
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(-1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(-1, game)))
                return
            if self._try_header_snake_direction((0, -1)):
                return
            self._set_selected_index(max(0, self.state.selected_index - 1))

        @bindings.add("down")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                self._command_history_move(1)
                return
            if self._artifact_chat_focus_active():
                self._artifact_chat_history_move(1)
                return
            if self._chat_focus_active():
                self._chat_history_move(1)
                return
            if self._audit_viewer_active():
                self._audit_viewer_scroll_vertical(1)
                return
            if self._template_editor_active():
                self._template_editor_move_cursor_vertical(1)
                return
            if bool(game.get("ai_snake_config_open")) and self.state.focus is FocusPane.CONTENT:
                if self._ai_snake_config_combo_active(game):
                    self._ai_snake_config_combo_move(1)
                else:
                    self._set_state(self.state.with_updates(selected_index=self._ai_snake_config_next_index(1, game)))
                return
            if self._try_header_snake_direction((0, 1)):
                return
            self._set_selected_index(self._clamp_down())

        @bindings.add(key_for_action("next_section", "c-n"))
        def _(event) -> None:
            self._normal_or_text("n", lambda: self._run_command(":next"))

        @bindings.add("<any>")
        def _(event) -> None:
            game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data == "\x7f":
                    # \x7f (DEL) is not bound by the specific backspace/c-h binding,
                    # so handle it here. \x08 (c-h) is already handled by the specific
                    # binding and must NOT be handled here too (double-fire in pt3).
                    self._command_backspace()
                    return
                if data and data.isprintable():
                    self._append_command(data)
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
            if self._audit_viewer_active():
                return
            if self._template_editor_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._template_editor_insert_text(data)
                return
            if self._ai_snake_config_combo_active(game):
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._ai_snake_config_combo_append_filter(data)
                return
            if self._snake_message_mode_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._snake_message_append(data)
                return

        @bindings.add(key_for_action("scroll_page_up", "pageup"))
        def _(event) -> None:
            self._scroll_active_panel(direction="page_up")

        @bindings.add(key_for_action("scroll_page_down", "pagedown"))
        def _(event) -> None:
            self._scroll_active_panel(direction="page_down")

        @bindings.add(key_for_action("scroll_line_up", "s-up"))
        @bindings.add("c-up")
        def _(event) -> None:
            self._scroll_active_panel(direction="line_up")

        @bindings.add(key_for_action("scroll_line_down", "s-down"))
        @bindings.add("c-down")
        def _(event) -> None:
            self._scroll_active_panel(direction="line_down")

        @bindings.add(key_for_action("scroll_home", "s-home"))
        def _(event) -> None:
            self._scroll_active_panel(direction="home")

        @bindings.add(key_for_action("scroll_end", "s-end"))
        def _(event) -> None:
            self._scroll_active_panel(direction="end")

        @bindings.add(key_for_action("scroll_left", "s-left"))
        @bindings.add("c-left")
        def _(event) -> None:
            self._h_scroll_center(delta=-4)

        @bindings.add(key_for_action("scroll_right", "s-right"))
        @bindings.add("c-right")
        def _(event) -> None:
            self._h_scroll_center(delta=4)

        @bindings.add(key_for_action("scroll_left_page", "s-pageup"))
        @bindings.add("c-pageup")
        def _(event) -> None:
            self._h_scroll_center(delta=-20)

        @bindings.add(key_for_action("scroll_right_page", "s-pagedown"))
        @bindings.add("c-pagedown")
        def _(event) -> None:
            self._h_scroll_center(delta=20)

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
                ctrl_held=parsed[5] if len(parsed) > 5 else False,
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

    def _get_scroll_manager(self):
        from client_surfaces.operator_tui.scroll.scroll_manager import ScrollManager
        from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
        if not hasattr(self, "_scroll_manager_instance"):
            self._scroll_manager_instance = ScrollManager()
            self._scroll_manager_instance.register(
                ScrollContext(id="chat_panel", label="Chat", content_height=100, viewport_height=20)
            )
            self._scroll_manager_instance.register(
                ScrollContext(id="main_content", label="Content", content_height=100, viewport_height=20)
            )
            self._scroll_manager_instance.register(
                ScrollContext(id="center_viewport", label="Visual Viewport", content_height=1, viewport_height=1)
            )
        return self._scroll_manager_instance

    def _get_focus_manager(self):
        from client_surfaces.operator_tui.focus.focus_manager import FocusManager
        if not hasattr(self, "_focus_manager_instance"):
            self._focus_manager_instance = FocusManager()
            self._focus_manager_instance.register_scroll_context("chat_panel", "chat_panel")
            self._focus_manager_instance.register_scroll_context("main_content", "main_content")
            self._focus_manager_instance.register_scroll_context("artifact_panel", "artifact_panel")
            self._focus_manager_instance.register_scroll_context("center_viewport", "center_viewport")
        return self._focus_manager_instance

    def _sync_scroll_focus_and_mouse_regions(
        self,
        *,
        width: int,
        height: int,
        content_width: int,
        body_start: int,
        body_height: int,
    ) -> None:
        """Keep keyboard and mouse scroll routing aligned with the visible panes."""
        sm = self._get_scroll_manager()
        fm = self._get_focus_manager()
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}

        active_focus = {
            FocusPane.NAVIGATION: "nav_panel",
            FocusPane.CONTENT: "center_viewport" if bool(game.get("visual_viewport_enabled")) else "main_content",
            FocusPane.DETAIL: "detail_panel",
            FocusPane.HEADER: "main_content",
        }.get(self.state.focus, "main_content")
        if self._chat_focus_active():
            active_focus = "chat_panel"
        fm.set_active(active_focus)

        meta = dict(game.get("visual_viewport_scene_meta") or {})
        content_lines = max(1, int(meta.get("content_lines") or body_height))
        sm.update("center_viewport", content_height=content_lines, viewport_height=max(1, body_height))

        try:
            from client_surfaces.operator_tui.input.mouse_router import MouseRouter, PanelRect
            mr = getattr(self, "_mouse_router_instance", None)
            if mr is None:
                self._mouse_router_instance = MouseRouter()
                mr = self._mouse_router_instance
            mr.clear_panels()
            left_width = 22
            detail_width = 34
            content_x1 = left_width + 2
            content_x2 = min(max(0, int(width) - detail_width - 5), content_x1 + max(1, content_width) - 1)
            detail_x1 = content_x2 + 3
            detail_x2 = min(max(0, int(width) - 1), detail_x1 + detail_width - 1)
            body_y1 = max(0, int(body_start))
            body_y2 = min(max(0, int(height) - 4), body_y1 + max(1, body_height) - 1)
            mr.register_panel(PanelRect(0, body_y1, left_width - 1, body_y2, "nav_panel", "main_content"))
            mr.register_panel(PanelRect(content_x1, body_y1, content_x2, body_y2, "center_viewport", "center_viewport"))
            mr.register_panel(PanelRect(detail_x1, body_y1, detail_x2, body_y2, "detail_panel", "chat_panel"))
        except Exception:
            pass

    def _scroll_active_panel(self, direction: str) -> None:
        if self._chat_focus_active():
            delta_map = {"page_up": -10, "page_down": 10, "line_up": -1, "line_down": 1, "home": -9999, "end": 9999}
            self._chat_scroll(delta_map.get(direction, 0))
            return
        # :ask-Modus: lange AI-Antwort im mittleren Pane scrollen
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if (
            str(game.get("tutor_ask_question") or "").strip()
            and self.state.focus is FocusPane.CONTENT
        ):
            delta_map = {"page_up": -10, "page_down": 10, "line_up": -1, "line_down": 1, "home": -9999, "end": 9999}
            delta = delta_map.get(direction, 0)
            if delta:
                raw_offset = game.get("chat_long_message_scroll_offset") or 0
                try:
                    cur = int(str(raw_offset))
                except (TypeError, ValueError):
                    cur = 0
                # Obergrenze wird im Renderer geclampt; hier großzügig lassen.
                new_offset = max(0, cur + delta)
                game["chat_long_message_scroll_offset"] = new_offset
                self._set_state(self.state.with_updates(header_logo_game=game))
                return
        sm = self._get_scroll_manager()
        fm = self._get_focus_manager()
        ctx_id = fm.active_scroll_context_id()
        if ctx_id is None:
            self._set_state(self.state.with_updates(status_message="kein scrollbarer Bereich fokussiert"))
            return
        ctx = sm.get(ctx_id)
        if ctx is None:
            return
        moved = False
        if direction == "page_up":
            moved = ctx.scroll_page_up()
        elif direction == "page_down":
            moved = ctx.scroll_page_down()
        elif direction == "line_up":
            moved = ctx.scroll_line_up()
        elif direction == "line_down":
            moved = ctx.scroll_line_down()
        elif direction == "home":
            moved = ctx.scroll_home()
        elif direction == "end":
            moved = ctx.scroll_end()
        if moved:
            game = dict(self.state.header_logo_game or self._default_header_snake())
            game[f"scroll_offset_{ctx_id}"] = ctx.offset
            if ctx_id == "center_viewport":
                game["visual_viewport_force_render"] = True
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"scroll: {ctx.label} {ctx.offset}/{ctx.max_scroll}"))

    def _h_scroll_center(self, delta: int) -> None:
        """Horizontal scroll for the center viewport (Markdown/Mermaid view)."""
        game = dict(self.state.header_logo_game or self._default_header_snake())
        meta = dict(game.get("visual_viewport_scene_meta") or {})
        max_line_width = int(meta.get("max_line_width") or 0)
        viewport_width = int(meta.get("viewport_width") or 0)
        if viewport_width <= 0:
            viewport_width = max(1, shutil.get_terminal_size((120, 32)).columns - 22 - 34 - 6)
        max_offset = max(0, max_line_width - viewport_width)
        current = int(game.get("center_h_scroll_offset") or 0)
        new_offset = max(0, min(max_offset, current + int(delta)))
        game["center_h_scroll_offset"] = new_offset
        game["visual_viewport_force_render"] = True
        # Propagate to view instance if available
        try:
            runtime = self._ensure_visual_runtime()
            view = runtime.get_view_instance("markdown_mermaid_document")
            if view is not None and hasattr(view, "apply_h_scroll_offset"):
                view.apply_h_scroll_offset(new_offset)
        except Exception:
            pass
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"h-scroll: {new_offset}/{max_offset}"))

    def _toggle_visual_view_switcher_overlay(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        current = bool(game.get("visual_view_switcher_overlay_visible", False))
        game["visual_view_switcher_overlay_visible"] = not current
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="View-Leiste: an" if game["visual_view_switcher_overlay_visible"] else "View-Leiste: aus",
            )
        )

    def _next_visual_view(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["visual_viewport_cycle_next"] = True
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="view: nächste"))

    def _previous_visual_view(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["visual_viewport_cycle_previous"] = True
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="view: vorherige"))

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
        try:
            from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings

            save_tui_chat_settings({"chat_panel_open": bool(game.get("chat_panel_open"))})
        except Exception:
            pass
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
        if self._snake_mode_active() or bool((self.state.header_logo_game or {}).get("chat_panel_open")):
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
            game["chat_panel_open"] = True
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
        game["tutor_ask_section_id"] = self.state.section_id
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
            text = sanitize_text(str(msg.get("text") or ""), max_len=6000)
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

    def _current_rendered_text(self) -> str:
        rendered = str(self._rendered_text or "")
        if rendered.strip():
            return rendered
        return self._render()

    def _copy_tui_snapshot(self) -> None:
        game = dict(self.state.header_logo_game or {})
        copied = rendered_tui_snapshot_text(self._current_rendered_text())
        game["clipboard"] = copied
        ok = self._copy_to_system_clipboard(copied) if copied.strip() else False
        status = "tui snapshot: intern + System-Zwischenablage" if ok else "tui snapshot: intern"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    def _save_tui_snapshot(self) -> None:
        try:
            target = write_tui_snapshot(self._current_rendered_text())
        except OSError as exc:
            self._set_state(self.state.with_updates(status_message=f"tui snapshot speichern fehlgeschlagen: {exc}"))
            return
        game = dict(self.state.header_logo_game or {})
        game["last_tui_snapshot_path"] = str(target)
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"tui snapshot gespeichert: {target}",
            )
        )

    def _open_latest_long_chat_message(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        active_view = str(game.get("visual_viewport_active_view") or game.get("visual_runtime_status", {}).get("active_view") or "")

        # If center view is already showing a long chat message, toggle render mode
        if is_showing_chat_long_message(game):
            new_mode = toggle_render_mode(game)
            mode_label = "Plain-Text" if new_mode == "plain" else "Markdown/Mermaid gerendert"
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message=f"Chat-Ansicht: {mode_label}",
                )
            )
            return

        # Generic document toggle: simple -> rendered -> mermaid -> simple
        if bool(game.get("visual_viewport_enabled")) and active_view == "markdown_mermaid_document":
            plain = bool(game.get("markdown_stream_plain"))
            mermaid_on = bool(game.get("markdown_mermaid_render_requested"))
            if plain:
                game["markdown_stream_plain"] = False
                game["markdown_mermaid_render_requested"] = False
                mode_label = "Markdown gerendert"
            elif not mermaid_on:
                game["markdown_stream_plain"] = False
                game["markdown_mermaid_render_requested"] = True
                mode_label = "Markdown+Mermaid"
            else:
                game["markdown_stream_plain"] = True
                game["markdown_mermaid_render_requested"] = False
                mode_label = "Simple"
            game["visual_viewport_force_render"] = True
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message=f"Doc-Ansicht: {mode_label}",
                )
            )
            return

        from client_surfaces.operator_tui.chat_state import get_chat_state
        chat = get_chat_state(game)
        channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
        active_ch_id = str(chat.get("active_channel") or "room:main")
        channel = channels.get(active_ch_id) if isinstance(channels, dict) else {}
        if not isinstance(channel, dict):
            channel = {}
        message = latest_long_message_for_channel(channel)
        if message is None:
            self._set_state(self.state.with_updates(status_message="keine lange Chatnachricht im aktiven Kanal"))
            return

        configure_middle_view_for_message(
            game,
            message,
            channel_id=active_ch_id,
            streaming=False,
            plain_text=True,
        )
        from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_chat_preview
        preview = str(message.get("text") or message.get("preview") or "Chat")
        label = tab_label_for_chat_preview(preview)
        vp_state = {"scroll_offset": 0, "preview": preview[:80]}
        next_state = open_or_activate_tab(
            self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT),
            section_id=self.state.section_id,
            kind="chat_viewport",
            label=label,
            viewport_state=vp_state,
        )
        game_out = dict(next_state.header_logo_game or game)
        game_out["visual_viewport_enabled"] = True
        self._set_state(next_state.with_updates(
            header_logo_game=game_out,
            status_message="lange Chatnachricht: Originalausgabe",
        ))

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

    def _audit_viewer_active(self) -> bool:
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        viewer = dict(game.get("audit_viewer") or {})
        return bool(viewer.get("active")) and self.state.section_id == "audit"

    def _audit_cleanup_confirm_mode_active(self) -> bool:
        if not self._audit_viewer_active():
            return False
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        viewer = dict(game.get("audit_viewer") or {})
        return str(viewer.get("mode") or "") == "confirm_cleanup"

    def _audit_cleanup_result_mode_active(self) -> bool:
        if not self._audit_viewer_active():
            return False
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        viewer = dict(game.get("audit_viewer") or {})
        return str(viewer.get("mode") or "") == "cleanup_result"

    def _audit_cleanup_set_choice(self, choice: str) -> None:
        game = dict(self.state.header_logo_game or {})
        viewer = dict(game.get("audit_viewer") or {})
        if str(viewer.get("mode") or "") != "confirm_cleanup":
            return
        normalized = "delete" if str(choice).strip().lower() == "delete" else "cancel"
        viewer["confirm_choice"] = normalized
        game["audit_viewer"] = viewer
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=(
                    "cleanup auswahl: Loeschen" if normalized == "delete" else "cleanup auswahl: Abbrechen"
                ),
            )
        )

    def _audit_cleanup_close_viewer(self, *, status_message: str) -> None:
        game = dict(self.state.header_logo_game or {})
        game["audit_viewer"] = {"active": False}
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                focus=FocusPane.CONTENT,
                status_message=status_message,
            )
        )

    def _audit_cleanup_show_result(self, *, title: str, summary: str) -> None:
        game = dict(self.state.header_logo_game or {})
        game["audit_viewer"] = {
            "active": True,
            "mode": "cleanup_result",
            "title": title,
            "group": "Data Cleanup",
            "text": summary,
            "view_line_offset": 0,
            "view_col_offset": 0,
        }
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                focus=FocusPane.CONTENT,
                status_message=summary,
            )
        )

    def _audit_cleanup_button_choice_from_click(self, *, x: int, y: int, width: int, height: int) -> str | None:
        if not self._audit_cleanup_confirm_mode_active():
            return None
        game = dict(self.state.header_logo_game or {})
        viewer = dict(game.get("audit_viewer") or {})
        text = str(viewer.get("text") or "")
        text_lines = text.splitlines() or [""]
        body_start = 10 if len(self.state.open_tabs) >= 2 else 9
        body_y1 = body_start
        body_height = max(3, int(height) - 5 - body_start)
        y_rel = int(y) - body_y1
        if y_rel < 0:
            return None
        pane_title_rows = 1
        viewer_header_rows = 3
        visible_rows = max(1, body_height - pane_title_rows - viewer_header_rows)
        view_line_offset = max(0, int(viewer.get("view_line_offset") or 0))
        max_line_offset = max(0, len(text_lines) - visible_rows)
        view_line_offset = min(view_line_offset, max_line_offset)
        end_line = min(len(text_lines), view_line_offset + visible_rows)
        button_row = pane_title_rows + viewer_header_rows + max(0, end_line - view_line_offset)
        if end_line < len(text_lines):
            button_row += 1
        button_row += 1
        if y_rel != button_row:
            return None
        left_width = 22
        detail_width = 34
        middle_width = max(12, int(width) - left_width - detail_width - 6)
        content_x1 = left_width + 2
        rel_x = int(x) - content_x1
        if rel_x < 0 or rel_x >= middle_width:
            return None
        button_line_mid = len("  [ Loeschen ]   [ Abbrechen ] ") // 2
        return "delete" if rel_x < button_line_mid else "cancel"

    def _audit_cleanup_handle_mouse_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        choice = self._audit_cleanup_button_choice_from_click(x=x, y=y, width=width, height=height)
        if choice is None:
            return False
        self._audit_cleanup_set_choice(choice)
        return self._confirm_audit_cleanup_action()

    def _selected_audit_entry(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if self.state.section_id != "audit":
            return None
        payload = dict((self.state.section_payloads or {}).get("audit") or {})
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None
        idx = max(0, min(len(items) - 1, int(self.state.selected_index)))
        entry = items[idx]
        if not isinstance(entry, dict):
            return None
        return payload, entry

    def _audit_viewer_viewport_metrics(self) -> tuple[int, int]:
        size = shutil.get_terminal_size((120, 32))
        width = max(72, int(size.columns))
        height = max(18, int(size.lines - 1))
        left_width = 22
        detail_width = 34
        middle_width = max(18, width - left_width - detail_width - 6)
        body_height = max(3, height - 5 - 8)
        pane_title_rows = 1
        viewer_header_rows = 3
        visible_rows = max(1, body_height - pane_title_rows - viewer_header_rows)
        visible_cols = max(8, middle_width - 8)
        return visible_rows, visible_cols

    def _audit_viewer_scroll_vertical(self, delta_lines: int) -> None:
        game = dict(self.state.header_logo_game or {})
        viewer = dict(game.get("audit_viewer") or {})
        if not bool(viewer.get("active")):
            return
        lines = str(viewer.get("text") or "").splitlines() or [""]
        visible_rows, _ = self._audit_viewer_viewport_metrics()
        max_offset = max(0, len(lines) - visible_rows)
        current = max(0, int(viewer.get("view_line_offset") or 0))
        viewer["view_line_offset"] = max(0, min(max_offset, current + int(delta_lines)))
        game["audit_viewer"] = viewer
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _audit_viewer_scroll_horizontal(self, delta_cols: int) -> None:
        game = dict(self.state.header_logo_game or {})
        viewer = dict(game.get("audit_viewer") or {})
        if not bool(viewer.get("active")):
            return
        lines = str(viewer.get("text") or "").splitlines() or [""]
        _, visible_cols = self._audit_viewer_viewport_metrics()
        max_width = max((len(line) for line in lines), default=0)
        max_offset = max(0, max_width - visible_cols)
        current = max(0, int(viewer.get("view_col_offset") or 0))
        viewer["view_col_offset"] = max(0, min(max_offset, current + int(delta_cols)))
        game["audit_viewer"] = viewer
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _open_audit_viewer_for_selected(self) -> bool:
        game = dict(self.state.header_logo_game or {})
        if self.state.section_id == "templates" and self.state.focus is FocusPane.CONTENT:
            return self._open_template_editor_for_selected()
        if self.state.focus is FocusPane.NAVIGATION:
            history_idx = (
                self.state.selected_index
                - len(SECTIONS)
                - self._template_nav_selectable_count()
                - self._audit_nav_selectable_count()
            )
            rows = long_message_history_rows(game)
            if 0 <= history_idx < len(rows) and configure_middle_view_for_history_entry(game, rows[history_idx]):
                self._set_state(
                    self.state.with_updates(
                        header_logo_game=game,
                        focus=FocusPane.CONTENT,
                        selected_index=0,
                        status_message="Chat-History: Originalausgabe",
                    )
                )
                return True
        if bool(game.get("ai_snake_config_open")):
            if self.state.focus is not FocusPane.CONTENT:
                self._set_state(self.state.with_updates(focus=FocusPane.CONTENT))
            if not self._ai_snake_config_combo_active(game):
                self._toggle_ai_snake_config_selected()
            return True
        selected = self._selected_audit_entry()
        if selected is None:
            return False
        payload, entry = selected
        dataset_id = str(entry.get("dataset_id") or entry.get("id") or "")
        datasets = payload.get("datasets")
        raw = datasets.get(dataset_id) if isinstance(datasets, dict) else None
        raw_dict = dict(raw) if isinstance(raw, dict) else {}
        if dataset_id.startswith("llm.requests.chat_prompt.") and isinstance(raw_dict.get("final_prompt_redacted"), str):
            text = str(raw_dict.get("final_prompt_redacted") or "").strip() or "{}"
            game = dict(self.state.header_logo_game or {})
            game["audit_viewer"] = {
                "active": True,
                "mode": "read_only",
                "dataset_id": dataset_id,
                "title": str(entry.get("title") or dataset_id or "dataset"),
                "group": str(entry.get("group") or ""),
                "text": text,
                "view_line_offset": 0,
                "view_col_offset": 0,
            }
            self._set_state(
                self.state.with_updates(
                    mode=OperatorMode.NORMAL,
                    focus=FocusPane.CONTENT,
                    header_logo_game=game,
                    status_message=f"audit viewer: {str(entry.get('title') or dataset_id)}",
                )
            )
            return True
        raw_kind = str(raw_dict.get("kind") or "")
        if raw_kind in {"cleanup_action", "cleanup_overview"}:
            details = [str(line) for line in list(raw_dict.get("details") or []) if str(line).strip()]
            if raw_kind == "cleanup_action":
                text_lines = [
                    "Bitte Loeschung bestaetigen.",
                    "",
                    *details,
                    "",
                    "Diese Aktion kann nicht rueckgaengig gemacht werden.",
                ]
                mode = "confirm_cleanup"
            else:
                text_lines = details
                mode = "read_only"
            text = "\n".join(text_lines).strip() or "{}"
            game = dict(self.state.header_logo_game or {})
            game["audit_viewer"] = {
                "active": True,
                "mode": mode,
                "dataset_id": dataset_id,
                "cleanup_action_id": str(raw_dict.get("cleanup_action_id") or ""),
                "confirm_choice": "cancel",
                "clear_runtime_chat": bool(raw_dict.get("clear_runtime_chat")),
                "clear_persisted_chat_history": bool(raw_dict.get("clear_persisted_chat_history")),
                "title": str(entry.get("title") or dataset_id or "dataset"),
                "group": str(entry.get("group") or ""),
                "text": text,
                "view_line_offset": 0,
                "view_col_offset": 0,
            }
            self._set_state(
                self.state.with_updates(
                    mode=OperatorMode.NORMAL,
                    focus=FocusPane.CONTENT,
                    header_logo_game=game,
                    status_message=(
                        f"cleanup bereit: {str(entry.get('title') or dataset_id)}"
                        if raw_kind == "cleanup_action"
                        else f"audit viewer: {str(entry.get('title') or dataset_id)}"
                    ),
                )
            )
            return True
        text: str
        if isinstance(raw, str):
            text = raw
        elif raw is None:
            text = "{}"
        else:
            text = json.dumps(raw, indent=2, ensure_ascii=False)
        game = dict(self.state.header_logo_game or {})
        game["audit_viewer"] = {
            "active": True,
            "dataset_id": dataset_id,
            "title": str(entry.get("title") or dataset_id or "dataset"),
            "group": str(entry.get("group") or ""),
            "text": text,
            "view_line_offset": 0,
            "view_col_offset": 0,
        }
        self._set_state(
            self.state.with_updates(
                mode=OperatorMode.NORMAL,
                focus=FocusPane.CONTENT,
                header_logo_game=game,
                status_message=f"audit viewer: {str(entry.get('title') or dataset_id)}",
            )
        )
        return True

    def _clear_runtime_chat_history(self, game: dict[str, Any]) -> None:
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

        chat = get_chat_state(game)
        channels = chat.get("channels")
        if isinstance(channels, dict):
            for channel in channels.values():
                if not isinstance(channel, dict):
                    continue
                channel["messages"] = []
                channel["unread"] = 0
        chat["chat_input_buffer"] = ""
        chat["chat_input_cursor"] = 0
        chat["chat_input_history_index"] = None
        chat["chat_input_saved_draft"] = ""
        chat["ai_typing"] = False
        set_chat_state(game, chat)

        game["artifact_chat_input"] = ""
        game["artifact_chat_cursor"] = 0
        game["artifact_chat_history"] = []
        game["artifact_chat_history_index"] = None
        game["artifact_chat_saved_draft"] = ""
        game["chat_long_message_history"] = []
        game["chat_long_message_markdown"] = ""
        game["chat_long_message_plain_text"] = ""
        game["chat_long_message_id"] = ""
        game["chat_memory_summary"] = ""
        game["chat_memory_summary_turn_count"] = 0

    def _clear_persisted_chat_history(self) -> None:
        from client_surfaces.operator_tui.config.user_config_manager import save_user_config

        save_user_config({"chat_input_history": []})

    def _confirm_audit_cleanup_action(self) -> bool:
        game = dict(self.state.header_logo_game or {})
        viewer = dict(game.get("audit_viewer") or {})
        if str(viewer.get("mode") or "") != "confirm_cleanup":
            return False
        choice = str(viewer.get("confirm_choice") or "cancel").strip().lower()
        title = str(viewer.get("title") or "Cleanup")
        if choice != "delete":
            self._audit_cleanup_show_result(title=title, summary="cleanup abgebrochen")
            return True
        action_id = str(viewer.get("cleanup_action_id") or "").strip()
        if not action_id:
            return False
        try:
            result = run_audit_cleanup_action(action_id)
        except Exception as exc:
            self._audit_cleanup_show_result(title=title, summary=f"cleanup fehlgeschlagen: {exc}")
            return True
        if bool(result.get("clear_runtime_chat")):
            self._clear_runtime_chat_history(game)
        if bool(result.get("clear_persisted_chat_history")):
            self._clear_persisted_chat_history()
        counts = dict(result.get("counts") or {})
        summary_parts = [f"{key}={value}" for key, value in counts.items() if int(value) > 0]
        summary = ", ".join(summary_parts) if summary_parts else "keine gespeicherten Einträge gefunden"
        self._audit_cleanup_show_result(title=title, summary=f"cleanup ausgefuehrt: {action_id} ({summary})")
        return True

    def _template_editor_active(self) -> bool:
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        editor = dict(game.get("template_editor") or {})
        return bool(editor.get("active")) and self.state.section_id == "templates"

    def _selected_template_entry(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        if self.state.section_id != "templates":
            return None
        payload = dict((self.state.section_payloads or {}).get("templates") or {})
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None
        idx = max(0, min(len(items) - 1, int(self.state.selected_index)))
        item = items[idx]
        if not isinstance(item, dict):
            return None
        kind = str(item.get("kind") or "")
        if kind not in {"template", "system_prompt", "blueprint"}:
            return None
        raw_id = str(item.get("raw_id") or "")
        raw_list = payload.get("blueprints_raw") if kind == "blueprint" else payload.get("templates_raw")
        if not isinstance(raw_list, list):
            return None
        raw = next((entry for entry in raw_list if isinstance(entry, dict) and str(entry.get("id") or "") == raw_id), {})
        if not isinstance(raw, dict):
            return None
        return payload, item, raw

    def _template_editor_text_for_item(self, *, kind: str, item: dict[str, Any], raw: dict[str, Any]) -> str:
        if kind == "blueprint":
            return json.dumps(raw, indent=2, ensure_ascii=False)
        return str(raw.get("prompt_template") or item.get("prompt_preview") or "")

    def _template_editor_viewport_metrics(self) -> tuple[int, int]:
        size = shutil.get_terminal_size((120, 32))
        width = max(72, int(size.columns))
        height = max(18, int(size.lines - 1))
        left_width = 22
        detail_width = 34
        middle_width = max(18, width - left_width - detail_width - 6)
        body_height = max(3, height - 5 - 8)
        pane_title_rows = 1
        editor_header_rows = 3
        visible_rows = max(1, body_height - pane_title_rows - editor_header_rows)
        text_prefix_width = 6  # f"{line_prefix} {row:>3} "
        visible_cols = max(8, middle_width - text_prefix_width)
        return visible_rows, visible_cols

    def _template_editor_ensure_cursor_visible(self, editor: dict[str, Any]) -> dict[str, Any]:
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        before = source[:cursor]
        cursor_line = before.count("\n")
        cursor_col = len(before.rsplit("\n", 1)[-1])
        lines = source.splitlines() or [""]
        max_line = max(0, len(lines) - 1)
        visible_rows, visible_cols = self._template_editor_viewport_metrics()

        line_offset = max(0, int(editor.get("view_line_offset") or 0))
        max_line_offset = max(0, len(lines) - visible_rows)
        line_offset = min(line_offset, max_line_offset)
        if cursor_line < line_offset:
            line_offset = cursor_line
        elif cursor_line >= line_offset + visible_rows:
            line_offset = max(0, cursor_line - visible_rows + 1)

        col_offset = max(0, int(editor.get("view_col_offset") or 0))
        max_col = max(0, len(lines[min(cursor_line, max_line)]) - 1)
        max_col_offset = max(0, max_col - visible_cols + 1)
        col_offset = min(col_offset, max_col_offset)
        if cursor_col < col_offset:
            col_offset = cursor_col
        elif cursor_col >= col_offset + visible_cols:
            col_offset = max(0, cursor_col - visible_cols + 1)

        editor["view_line_offset"] = max(0, line_offset)
        editor["view_col_offset"] = max(0, col_offset)
        return editor

    def _template_editor_scroll_vertical(self, delta_lines: int) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        lines = source.splitlines() or [""]
        visible_rows, _ = self._template_editor_viewport_metrics()
        max_offset = max(0, len(lines) - visible_rows)
        current = max(0, int(editor.get("view_line_offset") or 0))
        editor["view_line_offset"] = max(0, min(max_offset, current + int(delta_lines)))
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_set_cursor_from_content_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return False

        left_width = 22
        detail_width = 34
        middle_width = max(18, int(width) - left_width - detail_width - 6)
        body_start = 10 if len(self.state.open_tabs) >= 2 else 9
        content_x1 = left_width + 2
        content_x2 = content_x1 + middle_width - 1
        body_y1 = body_start
        body_height = max(3, int(height) - 5 - body_start)
        body_y2 = body_y1 + body_height - 1
        if not (content_x1 <= int(x) <= content_x2 and body_y1 <= int(y) <= body_y2):
            return False

        local_row = int(y) - body_y1
        local_col = int(x) - content_x1
        if local_row < 4:
            self._set_state(self.state.with_updates(focus=FocusPane.CONTENT))
            return True

        text = str(editor.get("text") or "")
        lines = text.splitlines() or [""]
        view_line_offset = max(0, int(editor.get("view_line_offset") or 0))
        view_col_offset = max(0, int(editor.get("view_col_offset") or 0))
        text_row = local_row - 4
        target_line = max(0, min(len(lines) - 1, view_line_offset + text_row))
        click_col = max(0, local_col - 6)
        target_col = max(0, min(len(lines[target_line]), view_col_offset + click_col))
        new_cursor = target_col
        for idx in range(target_line):
            new_cursor += len(lines[idx]) + 1
        editor["cursor"] = max(0, min(len(text), int(new_cursor)))
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT))
        return True

    def _open_template_editor_for_selected(self) -> bool:
        selected = self._selected_template_entry()
        if selected is None:
            return False
        _, item, raw = selected
        kind = str(item.get("kind") or "template")
        text = self._template_editor_text_for_item(kind=kind, item=item, raw=raw)
        game = dict(self.state.header_logo_game or {})
        game["template_editor"] = {
            "active": True,
            "template_id": str(raw.get("id") or item.get("raw_id") or ""),
            "kind": kind,
            "title": str(item.get("title") or ""),
            "text": text,
            "cursor": len(text),
            "view_line_offset": 0,
            "view_col_offset": 0,
            "dirty": False,
        }
        game["template_editor"] = self._template_editor_ensure_cursor_visible(dict(game["template_editor"]))
        self._set_state(
            self.state.with_updates(
                mode=OperatorMode.EDIT,
                focus=FocusPane.CONTENT,
                header_logo_game=game,
                markdown_source="",
                status_message=f"template editor: {str(item.get('title') or '')}",
            )
        )
        return True

    def _template_editor_insert_text(self, text: str) -> None:
        if not text:
            return
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        editor["text"] = source[:cursor] + text + source[cursor:]
        editor["cursor"] = cursor + len(text)
        editor["dirty"] = True
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        if cursor <= 0:
            return
        editor["text"] = source[: cursor - 1] + source[cursor:]
        editor["cursor"] = cursor - 1
        editor["dirty"] = True
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_delete(self) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        if cursor >= len(source):
            return
        editor["text"] = source[:cursor] + source[cursor + 1 :]
        editor["cursor"] = cursor
        editor["dirty"] = True
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_move_cursor(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        editor["cursor"] = max(0, min(len(source), cursor + int(delta)))
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_move_cursor_vertical(self, direction: int) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return
        source = str(editor.get("text") or "")
        cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
        before = source[:cursor]
        line_index = before.count("\n")
        col = len(before.rsplit("\n", 1)[-1])
        lines = source.splitlines() or [""]
        target_line = max(0, min(len(lines) - 1, line_index + int(direction)))
        target_col = min(col, len(lines[target_line]))
        new_cursor = 0
        for idx in range(target_line):
            new_cursor += len(lines[idx]) + 1
        new_cursor += target_col
        editor["cursor"] = max(0, min(len(source), new_cursor))
        editor = self._template_editor_ensure_cursor_visible(editor)
        game["template_editor"] = editor
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _template_editor_save(self) -> None:
        game = dict(self.state.header_logo_game or {})
        editor = dict(game.get("template_editor") or {})
        template_id = str(editor.get("template_id") or "").strip()
        editor_kind = str(editor.get("kind") or "template")
        if not template_id:
            self._set_state(self.state.with_updates(status_message="template editor: template_id fehlt"))
            return
        token = str(os.environ.get("ANANTA_AUTH_TOKEN") or os.environ.get("ANANTA_PASSWORD") or "").strip()
        if not token:
            self._set_state(self.state.with_updates(status_message="template editor: auth token fehlt"))
            return
        if editor_kind == "blueprint":
            try:
                blueprint_payload = json.loads(str(editor.get("text") or "{}"))
            except json.JSONDecodeError:
                self._set_state(self.state.with_updates(status_message="blueprint save failed: invalid JSON"))
                return
            if not isinstance(blueprint_payload, dict):
                self._set_state(self.state.with_updates(status_message="blueprint save failed: expected JSON object"))
                return
            endpoint = f"{str(self.state.endpoint).rstrip('/')}/teams/blueprints/{template_id}"
            allowed_keys = {"name", "description", "base_team_type_name", "roles", "artifacts"}
            request_payload = {key: blueprint_payload[key] for key in allowed_keys if key in blueprint_payload}
        else:
            endpoint = f"{str(self.state.endpoint).rstrip('/')}/templates/{template_id}"
            request_payload = {"prompt_template": str(editor.get("text") or "")}
        request_data = json.dumps(request_payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=request_data, method="PATCH")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=8.0) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            self._set_state(self.state.with_updates(status_message=f"template save failed: HTTP {exc.code}"))
            return
        except urllib.error.URLError as exc:
            self._set_state(self.state.with_updates(status_message=f"template save failed: {exc.reason}"))
            return
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        section_payloads = dict(self.state.section_payloads or {})
        templates_payload = dict(section_payloads.get("templates") or {})
        items = [dict(item) if isinstance(item, dict) else item for item in list(templates_payload.get("items") or [])]
        if editor_kind == "blueprint":
            blueprints_raw = [
                dict(item) if isinstance(item, dict) else item
                for item in list(templates_payload.get("blueprints_raw") or [])
            ]
            response_data = payload.get("data") if isinstance(payload, dict) else None
            for idx, entry in enumerate(blueprints_raw):
                if isinstance(entry, dict) and str(entry.get("id") or "") == template_id:
                    if isinstance(response_data, dict):
                        blueprints_raw[idx] = dict(response_data)
                    else:
                        blueprints_raw[idx] = {**entry, **request_payload}
            for item in items:
                if isinstance(item, dict) and str(item.get("raw_id") or "") == template_id:
                    item["title"] = str(request_payload.get("name") or item.get("title") or "")
                    item["description"] = str(request_payload.get("description") or item.get("description") or "")[:100]
                    if isinstance(request_payload.get("roles"), list):
                        item["roles_count"] = len(request_payload["roles"])
                    if isinstance(request_payload.get("artifacts"), list):
                        item["artifacts_count"] = len(request_payload["artifacts"])
            templates_payload["blueprints_raw"] = blueprints_raw
        else:
            templates_raw = [
                dict(item) if isinstance(item, dict) else item
                for item in list(templates_payload.get("templates_raw") or [])
            ]
            for entry in templates_raw:
                if isinstance(entry, dict) and str(entry.get("id") or "") == template_id:
                    entry["prompt_template"] = str(editor.get("text") or "")
            for item in items:
                if isinstance(item, dict) and str(item.get("raw_id") or "") == template_id:
                    item["prompt_preview"] = str(editor.get("text") or "")[:120].replace("\n", " ")
            templates_payload["templates_raw"] = templates_raw
        templates_payload["items"] = items
        section_payloads["templates"] = templates_payload
        editor["dirty"] = False
        game["template_editor"] = editor
        data = payload.get("data") if isinstance(payload, dict) else None
        warnings = ""
        if isinstance(data, dict) and data.get("warnings"):
            warnings = " (mit Warnungen)"
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                section_payloads=section_payloads,
                status_message=("blueprint gespeichert" if editor_kind == "blueprint" else f"template gespeichert{warnings}"),
            )
        )

    def _handle_enter_key(self) -> None:
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        if self.state.mode is OperatorMode.COMMAND:
            self._command_commit_history()
            self._run_command(self._command_buffer)
            return
        if self._artifact_chat_focus_active():
            self._artifact_chat_send_message()
            return
        if self._chat_focus_active():
            self._chat_send_message()
            return
        if self._audit_viewer_active():
            if self._audit_cleanup_result_mode_active():
                self._audit_cleanup_close_viewer(status_message="cleanup viewer geschlossen")
                return
            if self._confirm_audit_cleanup_action():
                return
            return
        if self._template_editor_active():
            self._template_editor_insert_text("\n")
            return
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
        if self.state.focus is FocusPane.NAVIGATION:
            if 0 <= self.state.selected_index < len(SECTIONS):
                section = SECTIONS[self.state.selected_index]
                self._run_command(f":section {section.id}")
                self._set_state(self.state.with_updates(focus=FocusPane.CONTENT, selected_index=0))
                return
            template_selection = self._template_nav_item_for_nav_index(self.state.selected_index)
            if template_selection is not None:
                item_index, item = template_selection
                next_state = self.state.with_updates(focus=FocusPane.CONTENT, selected_index=item_index, section_id="templates")
                self._set_state(next_state)
                if not self._open_template_editor_for_selected():
                    self._run_command(":inspect")
                return
            audit_selection = self._audit_nav_item_for_nav_index(self.state.selected_index)
            if audit_selection is not None:
                item_index, _ = audit_selection
                next_state = self.state.with_updates(focus=FocusPane.CONTENT, selected_index=item_index, section_id="audit")
                self._set_state(next_state)
                self._open_audit_viewer_for_selected()
                return
            history_idx = (
                self.state.selected_index
                - len(SECTIONS)
                - self._template_nav_selectable_count()
                - self._audit_nav_selectable_count()
            )
            game = dict(self.state.header_logo_game or self._default_header_snake())
            rows = long_message_history_rows(game)
            if 0 <= history_idx < len(rows) and configure_middle_view_for_history_entry(game, rows[history_idx]):
                self._set_state(
                    self.state.with_updates(
                        header_logo_game=game,
                        focus=FocusPane.CONTENT,
                        selected_index=0,
                        status_message="Chat-History: Originalausgabe",
                    )
                )
            return
        if self.state.focus is FocusPane.CONTENT and self.state.section_id == "templates":
            if self._open_template_editor_for_selected():
                return
        if self.state.focus is FocusPane.CONTENT and self.state.section_id == "audit":
            if self._open_audit_viewer_for_selected():
                return
        if self._snake_mode_active():
            # T04.04: Enter advances guided tour immediately
            game = self.state.header_logo_game or {}
            ts_raw = game.get("tutorial_state")
            if isinstance(ts_raw, dict) and ts_raw.get("guided"):
                self._advance_guided_tour_now()
            return
        if self.state.focus is FocusPane.HEADER:
            from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, cycle_value

            if 0 <= self.state.selected_index < len(CONFIG_ITEMS):
                self._set_state(cycle_value(self.state, CONFIG_ITEMS[self.state.selected_index]))
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

    # ── Input history persistence ──────────────────────────────────────────────

    def _input_history_config(self) -> dict[str, Any]:
        """Return current input-history config from user.json."""
        try:
            from client_surfaces.operator_tui.config.user_config_manager import load_user_config
            cfg = load_user_config()
            return cfg
        except Exception:
            return {}

    def _load_input_histories(self) -> None:
        """Load persisted command and chat histories from user.json on startup."""
        try:
            cfg = self._input_history_config()
            if cfg.get("input_history_command_enabled", True):
                saved = cfg.get("command_input_history", [])
                if isinstance(saved, list):
                    self._command_history = [str(e) for e in saved if str(e).strip()]
            # Chat history is loaded into game state in _default_header_snake via _apply_input_history_to_game
        except Exception:
            pass

    def _apply_input_history_to_game(self, game: dict[str, Any]) -> None:
        """Inject persisted chat input history into game state (called from _default_header_snake)."""
        try:
            cfg = self._input_history_config()
            if cfg.get("input_history_chat_enabled", True):
                saved = cfg.get("chat_input_history", [])
                if isinstance(saved, list) and saved:
                    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
                    chat = get_chat_state(game)
                    existing = list(chat.get("chat_input_history") or [])
                    # Prepend persisted entries (avoid duplicates)
                    for entry in reversed(saved):
                        if entry not in existing:
                            existing.insert(0, entry)
                    max_entries = int(cfg.get("input_history_max_entries", 100))
                    chat["chat_input_history"] = existing[-max_entries:]
                    set_chat_state(game, chat)
        except Exception:
            pass

    def _save_command_to_history(self, text: str) -> None:
        """Persist a command to user.json if history is enabled."""
        try:
            cfg = self._input_history_config()
            if not cfg.get("input_history_command_enabled", True):
                return
            max_entries = int(cfg.get("input_history_max_entries", 100))
            history = list(self._command_history)[-max_entries:]
            from client_surfaces.operator_tui.config.user_config_manager import save_user_config
            save_user_config({"command_input_history": history})
        except Exception:
            pass

    def _save_chat_to_history(self, text: str) -> None:
        """Persist a chat input to user.json if history is enabled."""
        try:
            cfg = self._input_history_config()
            if not cfg.get("input_history_chat_enabled", True):
                return
            max_entries = int(cfg.get("input_history_max_entries", 100))
            # Read current persisted history
            current = cfg.get("chat_input_history", [])
            if not isinstance(current, list):
                current = []
            if not current or current[-1] != text:
                current = current + [text]
            current = current[-max_entries:]
            from client_surfaces.operator_tui.config.user_config_manager import save_user_config
            save_user_config({"chat_input_history": current})
        except Exception:
            pass

    def _command_commit_history(self) -> None:
        text = str(self._command_buffer).strip()
        if not text:
            return
        if not self._command_history or self._command_history[-1] != text:
            self._command_history.append(text)
        self._command_history = self._command_history[-100:]
        self._command_history_index = None
        self._command_saved_draft = ""
        # Persist to user.json
        self._save_command_to_history(text)

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

    def _exit_command_mode_for_global_shortcut(self) -> None:
        if self.state.mode is not OperatorMode.COMMAND:
            return
        game = dict(self.state.header_logo_game or {})
        self._command_buffer = ""
        self._command_cursor = 0
        self._command_history_index = None
        self._command_saved_draft = ""
        game["command_input_cursor"] = 0
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
            )
        )

    def _enter_command_mode_from_anywhere(self) -> None:
        if self._chat_focus_active():
            self._chat_focus_leave()
        if self._artifact_chat_focus_active():
            self._artifact_chat_focus_leave(clear=False)
        if self._snake_message_mode_active():
            self._snake_cancel_message()
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if bool(game.get("ai_snake_config_open")):
            game["ai_snake_config_open"] = False
            game["ai_snake_config_combo"] = {"open": False}
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                )
            )
        self._open_command_mode()

    def _sync_command_line_state(self) -> None:
        game = dict(self.state.header_logo_game or {})
        game["command_input_cursor"] = max(0, min(len(self._command_buffer), int(self._command_cursor)))
        # Expose history count for renderer hint display
        n = len(self._command_history)
        game["_command_history_count"] = n if n > 0 else None
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
        if self._external_window_controller is not None:
            try:
                self._external_window_controller.close()
            except Exception:
                pass
        self._flush_config_on_exit()
        event.app.exit()

    def _flush_config_on_exit(self) -> None:
        """Flush all AI-Snake config to user.json and global ~/.anana/user.json on Ctrl-Q."""
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
        views.register_factory("markdown_mermaid_document", lambda: MarkdownMermaidDocumentView())

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
        # Browser mode owns the center pane while active; pause visual viewport rendering.
        if bool(game.get("center_browser_active")):
            game["visual_viewport"] = {"enabled": False}
            self.state = self.state.with_updates(header_logo_game=game)
            return
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
        force_render = False
        if requested_view:
            if runtime.switch_view(requested_view):
                game["visual_viewport_active_view"] = requested_view
            game.pop("visual_viewport_active_view_request", None)
            force_render = True

        left_width = 22
        detail_width = 34
        middle_width = max(18, int(width) - left_width - detail_width - 6)
        body_height = max(3, int(height) - 5 - 8)
        body_start = 8
        self._sync_scroll_focus_and_mouse_regions(
            width=width,
            height=height,
            content_width=middle_width,
            body_start=body_start,
            body_height=body_height,
        )
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
        # Propagate scroll offset from shared ScrollManager to markdown view (MDP-005)
        scroll_offset_for_view = 0
        try:
            sm = self._get_scroll_manager()
            sc = sm.get("center_viewport")
            if sc is not None:
                scroll_offset_for_view = sc.offset
                active_view_id = str(game.get("visual_viewport_active_view") or "")
                if active_view_id == "markdown_mermaid_document":
                    view_instance = runtime.get_view_instance("markdown_mermaid_document") if hasattr(runtime, "get_view_instance") else None
                    if view_instance is not None and hasattr(view_instance, "apply_scroll_offset"):
                        view_instance.apply_scroll_offset(scroll_offset_for_view)
        except Exception:
            pass

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
            "markdown_text": str(game.get("chat_long_message_markdown") or ""),
            "markdown_plain_text": str(game.get("chat_long_message_plain_text") or ""),
            "markdown_auto_follow": bool(game.get("markdown_auto_follow")),
            "markdown_stream_plain": bool(game.get("markdown_stream_plain")),
            "chat_long_message_streaming": bool(game.get("chat_long_message_streaming")),
            "markdown_mermaid_render_requested": bool(game.get("markdown_mermaid_render_requested")),
            "markdown_mermaid_config": dict(game.get("markdown_mermaid_config") or {}),
            "scroll_offset": scroll_offset_for_view,
            "h_scroll_offset": int(game.get("center_h_scroll_offset") or 0),
            "theme_version": "default",
        }
        previous_frame_lines = [
            str(row) for row in (game.get("visual_viewport_frame_lines") or []) if isinstance(row, str)
        ]
        force_render = force_render or bool(game.pop("visual_viewport_force_render", False)) or not previous_frame_lines
        frame = runtime.render_frame(region=region, now=time.monotonic(), state=state_map, force=force_render)
        frame_lines: list[str] = list(previous_frame_lines)
        if frame is not None and frame.frame_type == "ansi" and isinstance(frame.payload, list):
            frame_lines = [str(row) for row in frame.payload[:body_height]]
            # Extract scene metadata from frame for scrollbar rendering
            if frame.metadata:
                game["visual_viewport_scene_meta"] = {
                    k: frame.metadata.get(k)
                    for k in (
                        "content_lines",
                        "max_line_width",
                        "scroll_offset",
                        "h_offset",
                        "mermaid_renderer_used",
                        "mermaid_fallback_count",
                        "mermaid_cache_hits",
                        "mermaid_cache_misses",
                        "docs_graphics_profile",
                        "docs_graphics_wsl2_detected",
                    )
                    if frame.metadata.get(k) is not None
                }
                game["visual_viewport_scene_meta"]["viewport_width"] = middle_width
                game["visual_viewport_scene_meta"]["viewport_height"] = body_height
                try:
                    sm = self._get_scroll_manager()
                    sc = sm.get("center_viewport")
                    if sc is not None:
                        sc.update_dimensions(
                            content_height=max(1, int(game["visual_viewport_scene_meta"].get("content_lines") or body_height)),
                            viewport_height=max(1, body_height),
                        )
                except Exception:
                    pass
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
        new_state = self.state.with_updates(header_logo_game=game)
        scroll_now = int(game.get("scroll_offset_center_viewport") or 0)
        from client_surfaces.operator_tui.tab_manager import save_scroll_to_active_tab
        self.state = save_scroll_to_active_tab(new_state, scroll_now)

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
