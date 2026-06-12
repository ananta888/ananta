from __future__ import annotations

import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.models import FocusPane
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState
from client_surfaces.operator_tui.windowing.view_models.ai_snake_window_model import build_ai_snake_window_model
from client_surfaces.operator_tui.windowing.view_models.center_window_model import build_center_window_model

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def ensure_external_window_controller(tui: InteractiveOperatorTui) -> ExternalWindowController:
    if tui._external_window_controller is None:
        from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
        from client_surfaces.operator_tui.windowing.backends.wslg_webview_backend import WslgWebviewBackend
        tui._external_window_controller = ExternalWindowController(
            surface=WslgWebviewBackend(),
            bridge=ExternalWindowBridgeServer(),
        )
    return tui._external_window_controller


def tick_center_browser(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    if not bool(game.get("center_browser_active")):
        if tui._browser_controller is not None:
            try:
                tui._browser_controller.exit_browser_mode()
            except Exception:
                pass
            tui._browser_controller = None
        return

    url = str(game.get("center_browser_url") or "")
    status = str(game.get("center_browser_status") or "")

    if tui._browser_controller is None and status == "requested":
        try:
            from client_surfaces.operator_tui.visual.browser.browser_mode_controller import BrowserModeController
            from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot
            size = shutil.get_terminal_size((120, 32))
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
                snap = CenterContentSnapshot(
                    content_type="plain_text", title="Browser",
                    source_text="(kein Inhalt)", html_text="", metadata={},
                    scroll_position=0, unsupported_reason="",
                )
                ctrl.enter_browser_mode(snap, cols=center_w, rows=body_h)
            tui._browser_controller = ctrl
            game["center_browser_status"] = "active"
            game["center_browser_error"] = str(ctrl.error_message) if hasattr(ctrl, "error_message") else ""
            tui._set_state(tui.state.with_updates(header_logo_game=game))
        except Exception as exc:
            game["center_browser_active"] = False
            game["center_browser_status"] = "error"
            game["center_browser_error"] = str(exc)
            game["_cmd_feedback"] = f"browser error: {exc}"
            game["_cmd_feedback_at"] = time.monotonic()
            tui._browser_controller = None
            tui._set_state(tui.state.with_updates(header_logo_game=game))
        return

    if tui._browser_controller is not None:
        try:
            chunk = tui._browser_controller.tick()
            if chunk:
                existing = bytes(game.get("_browser_frame_bytes") or b"")
                combined = (existing + chunk)[-65536:]
                game["_browser_frame_bytes"] = combined
                tui._set_state(tui.state.with_updates(header_logo_game=game))
            if not tui._browser_controller.is_running():
                game["center_browser_active"] = False
                game["center_browser_status"] = "stopped"
                game["_cmd_feedback"] = "browser: beendet"
                game["_cmd_feedback_at"] = time.monotonic()
                tui._browser_controller = None
                tui._set_state(tui.state.with_updates(header_logo_game=game))
        except Exception as exc:
            game["center_browser_active"] = False
            game["center_browser_status"] = "error"
            game["_cmd_feedback"] = f"browser error: {exc}"
            game["_cmd_feedback_at"] = time.monotonic()
            tui._browser_controller = None
            tui._set_state(tui.state.with_updates(header_logo_game=game))


def tick_external_window(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    command = str(game.pop("center_window_command", "")).strip().lower()
    requested_view_mode = str(game.pop("center_window_view_mode_request", "")).strip().lower()
    if command:
        ctrl = ensure_external_window_controller(tui)
        if command == "center.window.open":
            st = ctrl.open(auth_context=_build_auth_context_for_window(tui))
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
        game["_cmd_feedback"] = msg
        game["_cmd_feedback_at"] = time.monotonic()
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=msg))

    if requested_view_mode in {"simple", "doc", "snake"}:
        apply_external_window_action(tui, f"view.{requested_view_mode}")

    ctrl = tui._external_window_controller
    if ctrl is None:
        return
    st_now = ctrl.status()
    game = dict(tui.state.header_logo_game or {})
    game["center_window_state"] = st_now.state.value
    game["center_window_backend"] = st_now.backend
    game["center_window_bridge_port"] = st_now.bridge_port
    game["center_window_bridge_connected"] = bool(st_now.bridge_running)
    game["center_window_dropped_events"] = int(st_now.dropped_events)
    game["center_window_rejected_actions"] = int(st_now.rejected_actions)
    game["center_window_accepted_actions"] = int(st_now.accepted_actions)
    game["center_window_reason"] = st_now.reason
    game["center_window_active"] = st_now.state in {ExternalWindowState.ACTIVE, ExternalWindowState.STARTING}
    tui.state = tui.state.with_updates(header_logo_game=game)
    ctrl.publish_state(build_external_window_state_payload(tui))
    for event in ctrl.drain_events():
        apply_external_window_action(
            tui,
            str(getattr(event, "action_id", "")),
            dict(getattr(event, "args", {}) or {}),
        )


def build_external_window_state_payload(tui: InteractiveOperatorTui) -> dict[str, Any]:
    game = dict(tui.state.header_logo_game or {})
    center_model = build_center_window_model(state=tui.state, game=game)
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


def apply_external_window_action(tui: InteractiveOperatorTui, action_id: str, args: dict[str, Any] | None = None) -> None:
    aid = str(action_id or "").strip()
    if not aid:
        return
    if aid == "view.simple":
        game = dict(tui.state.header_logo_game or {})
        game["center_window_view_mode"] = "simple"
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="window action: view simple"))
        return
    if aid == "view.doc":
        game = dict(tui.state.header_logo_game or {})
        game["center_window_view_mode"] = "doc"
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="window action: view doc"))
        tui._run_command(":doc switch")
        return
    if aid == "view.snake":
        game = dict(tui.state.header_logo_game or {})
        game["center_window_view_mode"] = "snake"
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="window action: view snake"))
        if not bool(game.get("snake_mode")):
            tui._toggle_snake_mode()
        return
    if aid == "view.next":
        tui._next_visual_view()
        return
    if aid == "view.previous":
        tui._previous_visual_view()
        return
    if aid == "focus.center":
        tui._set_state(tui.state.with_updates(focus=FocusPane.CONTENT, status_message="window action: focus center"))
        return
    if aid == "focus.nav":
        tui._set_state(tui.state.with_updates(focus=FocusPane.NAVIGATION, status_message="window action: focus nav"))
        return
    game = dict(tui.state.header_logo_game or {})
    if aid == "snake.pause":
        if bool(game.get("snake_mode")) and not bool(game.get("paused")):
            tui._toggle_snake_pause()
        return
    if aid == "snake.resume":
        if bool(game.get("snake_mode")) and bool(game.get("paused")):
            tui._toggle_snake_pause()
        return
    if aid == "settings.reload":
        apply_settings_from_browser(tui)
        return


def apply_settings_from_browser(tui: InteractiveOperatorTui) -> None:
    from client_surfaces.operator_tui.config.user_config_manager import load_user_config
    _SKIP = frozenset({"chat_input_history", "command_input_history"})
    try:
        fresh = load_user_config()
    except Exception:
        return
    game = dict(tui.state.header_logo_game or {})
    for key, value in fresh.items():
        if key not in _SKIP:
            game[key] = value
    tui._set_state(tui.state.with_updates(
        header_logo_game=game,
        status_message="Browser: Einstellungen übernommen",
    ))


def _build_auth_context_for_window(tui: InteractiveOperatorTui) -> dict[str, str]:
    game = dict(tui.state.header_logo_game or {})
    oidc_token = str(game.get("oidc_token") or "")
    hub_url = str(tui.state.endpoint or "").rstrip("/")
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
