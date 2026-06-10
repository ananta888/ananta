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
                st = ctrl.open(auth_context=self._build_auth_context_for_window())
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
            self._apply_external_window_action(
                str(getattr(event, "action_id", "")),
                dict(getattr(event, "args", {}) or {}),
            )

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

    def _apply_external_window_action(self, action_id: str, args: dict[str, Any] | None = None) -> None:
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
        if aid == "settings.reload":
            self._apply_settings_from_browser()
            return

    def _apply_settings_from_browser(self) -> None:
        from client_surfaces.operator_tui.config.user_config_manager import load_user_config
        _SKIP = frozenset({"chat_input_history", "command_input_history"})
        try:
            fresh = load_user_config()
        except Exception:
            return
        game = dict(self.state.header_logo_game or {})
        for key, value in fresh.items():
            if key not in _SKIP:
                game[key] = value
        self._set_state(self.state.with_updates(
            header_logo_game=game,
            status_message="Browser: Einstellungen übernommen",
        ))

    def _build_auth_context_for_window(self) -> dict[str, str]:
        game = dict(self.state.header_logo_game or {})
        oidc_token = str(game.get("oidc_token") or "")
        hub_url = str(self.state.endpoint or "").rstrip("/")
        hub_token = ""
        if hub_url:
            try:
                hub_raw = (
                    os.environ.get("ANANTA_AUTH_TOKEN") or os.environ.get("ANANTA_PASSWORD") or ""
                ).strip()
                if not hub_raw:
                    from client_surfaces.operator_tui.app import _load_env_file
                    _env = _load_env_file()
                    hub_raw = (
                        _env.get("ANANTA_AUTH_TOKEN") or _env.get("ANANTA_PASSWORD") or ""
                    ).strip()
                if hub_raw:
                    from client_surfaces.operator_tui.hub_loader import resolve_token
                    hub_token = resolve_token(hub_url, hub_raw)
            except Exception:
                pass
        return {"hub_url": hub_url, "hub_token": hub_token, "oidc_token": oidc_token}

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
        from client_surfaces.operator_tui.chat_state import (
            get_chat_state, set_chat_state, switch_channel, get_sessions,
        )
        chat = get_chat_state(game)
        channels_dict = chat.get("channels") or {}
        # Build the cycle order: the non-session channels first (room,
        # notes, system) and then the session channels in the order the
        # user has them in their session list. This makes the cycle
        # predictable — pressing the cycle key moves through the user's
        # sessions in order, with the non-session channels available as
        # waypoints.
        session_ids = [str(s.get("id") or "") for s in get_sessions(chat) if isinstance(s, dict)]
        preferred = [ch for ch in ["room:main", "notes:self", "system"] if ch in channels_dict]
        session_channels = [f"ai:{sid}" for sid in session_ids if f"ai:{sid}" in channels_dict]
        ordered = preferred + session_channels
        if not ordered:
            return
        current = str(chat.get("active_channel") or ordered[0])
        try:
            idx = ordered.index(current)
        except ValueError:
            # Current channel not in the ordered list — start from the
            # first session channel so the user can cycle forward
            # predictably.
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
