from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.models import OperatorState
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

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def visual_capabilities(tui: InteractiveOperatorTui) -> TerminalVisualCapabilities:
    term = dict(os.environ)
    return TerminalVisualCapabilities(
        ansi=True,
        sixel="sixel" in str(term.get("TERM", "")).lower() or str(term.get("ANANTA_TUI_FORCE_SIXEL", "")).strip() == "1",
        kitty_graphics=bool(str(term.get("KITTY_WINDOW_ID") or "").strip())
        or str(term.get("TERM", "")).lower() == "xterm-kitty",
        opengl_offscreen=str(term.get("ANANTA_TUI_VISUAL_OPENGL", "0")).strip().lower() in {"1", "true", "yes", "on"},
    )


def load_visual_viewport_config(tui: InteractiveOperatorTui) -> VisualViewportConfig:
    file_mapping: dict[str, Any] = {}
    cfg_path = Path("config/operator_tui_visual_viewport.default.json")
    if cfg_path.exists():
        try:
            parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                file_mapping = dict(parsed.get("visual_viewport") or {})
        except (OSError, json.JSONDecodeError) as exc:
            tui._visual_config_error = f"visual config fehler: {exc}"
            file_mapping = {}
    else:
        tui._visual_config_error = ""
    env_enabled = str(os.environ.get("ANANTA_TUI_VISUAL_VIEWPORT_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes", "on",
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
        if not tui._visual_config_error:
            tui._visual_config_error = ""
        return cfg
    except (TypeError, ValueError) as exc:
        tui._visual_config_error = f"visual config fehler: {exc}"
        return VisualViewportConfig()


def build_visual_runtime(tui: InteractiveOperatorTui) -> VisualRuntime:
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
    adapters.register_factory("sixel", lambda: SixelOutputAdapter(supported=tui._visual_capabilities().sixel))
    adapters.register_factory("kitty", lambda: KittyOutputAdapter(supported=tui._visual_capabilities().kitty_graphics))
    adapters.register_factory("noop_diagnostics", lambda: NoopDiagnosticsAdapter())

    return VisualRuntime(
        config=tui._visual_viewport_config,
        view_registry=views,
        renderer_registry=renderers,
        adapter_registry=adapters,
        capabilities=tui._visual_capabilities(),
    )


def ensure_visual_runtime(tui: InteractiveOperatorTui) -> VisualRuntime:
    if tui._visual_runtime is None:
        tui._visual_runtime = tui._build_visual_runtime()
    return tui._visual_runtime


def apply_visual_command_requests(tui: InteractiveOperatorTui, state: OperatorState) -> OperatorState:
    game = dict(state.header_logo_game or {})
    requested_view = str(game.get("visual_viewport_active_view_request") or "").strip()
    if not requested_view:
        return state
    runtime = tui._ensure_visual_runtime()
    ok = runtime.switch_view(requested_view)
    game.pop("visual_viewport_active_view_request", None)
    game["visual_viewport_enabled"] = True
    game["visual_viewport_active_view"] = requested_view if ok else str(runtime.status().active_view)
    status = f"visual view: {game['visual_viewport_active_view']}" if ok else f"visual view unbekannt: {requested_view}"
    return state.with_updates(header_logo_game=game, status_message=status)


def sync_visual_viewport_state(tui: InteractiveOperatorTui, *, width: int, height: int) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if bool(game.get("center_browser_active")):
        game["visual_viewport"] = {"enabled": False}
        tui.state = tui.state.with_updates(header_logo_game=game)
        return
    enabled = bool(game.get("visual_viewport_enabled", tui._visual_viewport_config.enabled))
    if not enabled:
        game["visual_viewport"] = {"enabled": False}
        if tui._visual_config_error:
            game["visual_runtime_status"] = {
                "runtime_error": tui._visual_config_error,
            }
        game.pop("visual_viewport_frame_lines", None)
        tui.state = tui.state.with_updates(header_logo_game=game)
        return

    runtime = tui._ensure_visual_runtime()
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
    tui._sync_scroll_focus_and_mouse_regions(
        width=width,
        height=height,
        content_width=middle_width,
        body_start=body_start,
        body_height=body_height,
    )
    px_w, px_h = derive_pixel_size(
        columns=middle_width,
        rows=body_height,
        default_pixel_width=tui._visual_viewport_config.default_pixel_width,
        default_pixel_height=tui._visual_viewport_config.default_pixel_height,
        max_pixel_width=tui._visual_viewport_config.max_pixel_width,
        max_pixel_height=tui._visual_viewport_config.max_pixel_height,
    )
    region = ViewportRegion(
        x=24,
        y=body_start,
        columns=middle_width,
        rows=body_height,
        pixel_width=px_w,
        pixel_height=px_h,
    )
    scroll_offset_for_view = 0
    try:
        sm = tui._get_scroll_manager()
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
                sm = tui._get_scroll_manager()
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
        "runtime_error": status.runtime_errors[-1] if status.runtime_errors else tui._visual_config_error,
    }
    game["visual_viewport"] = {"enabled": True}
    game["visual_viewport_active_view"] = status.active_view
    game["visual_viewport_active_renderer"] = status.active_renderer
    game["visual_viewport_active_adapter"] = status.active_adapter
    new_state = tui.state.with_updates(header_logo_game=game)
    scroll_now = int(game.get("scroll_offset_center_viewport") or 0)
    from client_surfaces.operator_tui.tab_manager import save_scroll_to_active_tab
    tui.state = save_scroll_to_active_tab(new_state, scroll_now)
