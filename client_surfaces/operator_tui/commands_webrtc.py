"""Center.browser and WebRTC command handlers for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

import html as _html

from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState




# ---------------------------------------------------------------------------
# center.browser commands (carbonyl-005)
# ---------------------------------------------------------------------------

def execute_center_browser_command(raw_command: str, state: OperatorState) -> CommandResult | None:
    """Handle center.browser.* commands. Returns None if not a browser command."""
    text = str(raw_command or "").strip().lstrip(":").lstrip("/").strip()
    parts = text.split()
    if not parts:
        return None

    cmd = parts[0].lower().replace("-", ".").replace("_", ".")
    # :cb <url> — short alias for center.browser.url
    if cmd == "cb":
        if len(parts) > 1:
            parts = ["center.browser.url"] + parts[1:]
            cmd = "center.browser.url"
        else:
            # :cb without args — show status
            from client_surfaces.operator_tui.visual.runtime.capability_detector import detect_carbonyl_browser
            cap = detect_carbonyl_browser()
            msg = f"browser: carbonyl {'verfügbar' if cap.available else 'nicht gefunden — ' + cap.unavailable_reason}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)

    def _center_webview_html(mode: str, game: dict[str, object]) -> str:
        snake_active = bool(game.get("active"))
        snake_paused = bool(game.get("paused"))
        snake_mode = str(game.get("ai_snake_mode") or "lurking_follow")
        runtime = str(game.get("ai_snake_runtime_status") or "idle")
        focus = str(state.focus.value if hasattr(state.focus, "value") else state.focus)
        section = str(state.section_id or "dashboard")
        status = _html.escape(str(state.status_message or "ready"))
        mode_label = _html.escape(mode)
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ananta Center WebView</title>
<style>
body{{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0b1220;color:#d8e2ff;margin:0;padding:16px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{border:1px solid #2b3a58;border-radius:10px;padding:12px;background:#101a2f}}
.h{{font-weight:700;color:#9ec5ff;margin-bottom:8px}}
.k{{color:#8aa2d3}} .v{{color:#f3f7ff}} .ok{{color:#7ee787}} .warn{{color:#ffd866}}
.hint{{margin-top:12px;color:#a7b7d9;font-size:12px;line-height:1.45}}
</style></head>
<body>
<div class="card"><div class="h">Ananta Hybrid Center</div>
<div><span class="k">mode:</span> <span class="v">{mode_label}</span></div>
<div><span class="k">section:</span> <span class="v">{_html.escape(section)}</span></div>
<div><span class="k">focus:</span> <span class="v">{_html.escape(focus)}</span></div>
<div><span class="k">status:</span> <span class="v">{status}</span></div></div>
<div class="grid">
<div class="card"><div class="h">AI-Snake</div>
<div><span class="k">active:</span> <span class="{'ok' if snake_active else 'warn'}">{snake_active}</span></div>
<div><span class="k">paused:</span> <span class="v">{snake_paused}</span></div>
<div><span class="k">mode:</span> <span class="v">{_html.escape(snake_mode)}</span></div>
<div><span class="k">runtime:</span> <span class="v">{_html.escape(runtime)}</span></div></div>
<div class="card"><div class="h">Steuerung (TUI bleibt Master)</div>
<div><span class="k">Ctrl+S</span> Snake an/aus</div>
<div><span class="k">Ctrl+P</span> Pause/Resume</div>
<div><span class="k">Ctrl+E</span> Chat-Fokus</div>
<div><span class="k">Ctrl+3</span> Center→Doc</div>
<div><span class="k">Ctrl+2</span> Browser/WebView toggle</div></div>
</div>
<div class="hint">Hinweis: Diese WebView ist eine externe Render-Surface. Die Orchestrierung/Steuerung bleibt in der TUI.</div>
</body></html>"""

    known = {
        "center.browser.toggle",
        "center.browser.open_current",
        "center.browser.exit",
        "center.browser.url",
        "center.browser.open",
        "center.webview.open",
        "center.webview.snake",
        "center.window.open",
        "center.window.close",
        "center.window.status",
        "center.window.restart",
        "center.window.view",
        "cwv",
        "cb",
        "cwo",
    }
    if cmd not in known:
        return None

    game = dict(state.header_logo_game or {})
    browser_active = bool(game.get("center_browser_active"))

    if cmd == "center.browser.exit" or (cmd == "center.browser.toggle" and browser_active):
        game["center_browser_active"] = False
        game["center_browser_status"] = "exited"
        game.pop("center_browser_url", None)
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message="browser mode: exited | center view restored",
            ),
            "center.browser.exit",
        )

    # :center.browser.url <url>  or  :browser <url>
    if cmd in {"center.browser.url", "center.browser.open"} or (
        cmd == "center.browser.toggle" and len(parts) > 1
    ):
        url = parts[1] if len(parts) > 1 else ""
        if not url:
            return CommandResult(
                state.with_updates(status_message="Usage: :center.browser.url <url>"),
                "center.browser.url",
                handled=False,
            )
        # Check carbonyl availability immediately
        from client_surfaces.operator_tui.visual.runtime.capability_detector import detect_carbonyl_browser
        cap = detect_carbonyl_browser()
        if not cap.available:
            msg = f"browser: carbonyl nicht gefunden — {cap.unavailable_reason} | npm install -g carbonyl"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        local_md_candidate = Path(url).expanduser()
        if url.lower().endswith(".md") and local_md_candidate.exists():
            msg = f"doc erkannt: :doc open {url}"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        # Normalise: add https:// if no scheme given
        if not url.startswith(("http://", "https://", "file://", "data:")):
            url = "https://" + url
        if url.startswith(("file://",)) and url.lower().endswith(".md"):
            msg = "doc erkannt: nutze :doc open <pfad.md> fuer markdown_mermaid_document view"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        game["center_browser_active"] = True
        game["center_browser_status"] = "requested"
        game["center_browser_url"] = url
        game["center_browser_allow_remote"] = True
        game["center_browser_render_mode"] = "raw_ansi"
        game.pop("center_browser_error", None)
        game.pop("_browser_frame_bytes", None)
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=(
                    f"browser: öffne {url} | "
                    f"{display_for_action('center_browser_toggle', 'Ctrl+2')} toggle | Esc exit"
                ),
            ),
            "center.browser.url",
        )

    if cmd in {"center.browser.open_current", "center.browser.toggle"}:
        game["center_browser_active"] = True
        game["center_browser_status"] = "requested"
        game.pop("center_browser_url", None)
        game["center_browser_render_mode"] = "raw_ansi"
        game.pop("center_browser_error", None)
        game.pop("_browser_frame_bytes", None)
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=(
                    "browser mode: activating | "
                    f"{display_for_action('center_browser_toggle', 'Ctrl+2')} toggle | Esc exit"
                ),
            ),
            "center.browser.open_current",
        )

    if cmd == "cwv":
        cmd = "center.webview.open"
    if cmd == "cwo":
        cmd = "center.window.open"

    if cmd == "center.window.view":
        mode = str(parts[1] if len(parts) > 1 else "").strip().lower()
        if mode not in {"simple", "doc", "snake"}:
            msg = "center.window.view <simple|doc|snake>"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        game["center_window_view_mode_request"] = mode
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"center window: view {mode} angefordert",
            ),
            cmd,
        )

    if cmd in {"center.window.open", "center.window.close", "center.window.status", "center.window.restart"}:
        game["center_window_command"] = cmd
        status_text = {
            "center.window.open": "center window: open angefordert",
            "center.window.close": "center window: close angefordert",
            "center.window.status": "center window: status angefordert",
            "center.window.restart": "center window: restart angefordert",
        }[cmd]
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=status_text,
            ),
            cmd,
        )

    if cmd in {"center.webview.open", "center.webview.snake"}:
        from client_surfaces.operator_tui.visual.runtime.capability_detector import detect_carbonyl_browser
        mode = "snake" if cmd.endswith(".snake") else "dashboard"
        cap = detect_carbonyl_browser()
        if not cap.available:
            msg = f"webview: carbonyl nicht gefunden — {cap.unavailable_reason} | npm install -g carbonyl"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        html_doc = _center_webview_html(mode, game)
        data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(html_doc, safe=":/?&=#,%+-._~")
        game["center_browser_active"] = True
        game["center_browser_status"] = "requested"
        game["center_browser_url"] = data_url
        game["center_browser_allow_remote"] = False
        game["center_browser_render_mode"] = "raw_ansi"
        game.pop("center_browser_error", None)
        game.pop("_browser_frame_bytes", None)
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"webview: {mode} aktiv | {display_for_action('center_browser_toggle', 'Ctrl+2')} toggle",
            ),
            "center.webview.open",
        )

    return None


# ---------------------------------------------------------------------------
# WebRTC DataChannel commands (Option C — realtime/ stack, separate from Hub Relay)
# ---------------------------------------------------------------------------

# Module-level registry: one session controller per session.
# Populated lazily when center.browser.webrtc.start is called.
_webrtc_controllers: dict[str, object] = {}


def _handle_webrtc_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    """Handle center.browser.webrtc.* TUI commands.

    These commands operate the Ananta WebRTC DataChannel stack (realtime/).
    They are completely separate from webrtc_transport.py (Hub Relay).

    Commands
    --------
    center.browser.webrtc.start [session_id]
        Start a new WebRTC session. Reads OIDC state from header_logo_game.
    center.browser.webrtc.stop [session_id]
        Stop the WebRTC session.
    center.browser.webrtc.status [session_id]
        Return current session status.
    center.browser.webrtc.accept_artifact <offer_id> [session_id]
        Accept a pending artifact offer.
    """
    try:
        from client_surfaces.operator_tui.realtime.webrtc_session_controller import WebRtcSessionController
        from client_surfaces.operator_tui.realtime.signaling_client import SignalingClient
        from client_surfaces.operator_tui.realtime.webrtc_policy import WebRtcPolicy
        from client_surfaces.operator_tui.realtime.webrtc_audit import WebRtcAuditLog, WebRtcAuditEvent, EVENT_SESSION_START, EVENT_SESSION_CLOSED, EVENT_ERROR
    except ImportError as exc:
        msg = f"WebRTC realtime stack not available: {exc}"
        return CommandResult(state.with_updates(status_message=msg), msg, handled=False)

    game = dict(state.header_logo_game or {})
    oidc_token = game.get("oidc_token") or ""
    oidc_subject_hash = ""
    if oidc_token:
        import hashlib as _hl
        oidc_subject_hash = _hl.sha256(oidc_token.encode()).hexdigest()[:16]

    # Resolve session_id: last arg if it doesn't look like an offer_id
    session_id = args[-1] if args and not command.endswith(".accept_artifact") else "default"
    if command.endswith(".accept_artifact"):
        session_id = args[-1] if len(args) > 1 else "default"

    if command == "center.browser.webrtc.start":
        if session_id in _webrtc_controllers:
            ctrl = _webrtc_controllers[session_id]
            status = ctrl.get_status()  # type: ignore[union-attr]
            if status.get("signaling") not in {"disconnected", "failed"}:
                msg = f"WebRTC session {session_id!r} already active: {status.get('signaling')}"
                return CommandResult(state.with_updates(status_message=msg), msg)

        # Read signaling config from game dict
        signaling_url = str(game.get("webrtc_signaling_url") or "")
        allowed_servers = list(game.get("webrtc_allowed_servers") or [])
        session_nonce = str(game.get("webrtc_session_nonce") or game.get("_share_session_nonce") or "")

        policy = WebRtcPolicy()
        signaling = SignalingClient(
            server_url=signaling_url or "wss://webrtc.ananta.de/signaling",
            allowed_servers=allowed_servers or ["wss://webrtc.ananta.de"],
            session_nonce=session_nonce,
        )
        ctrl = WebRtcSessionController(signaling_client=signaling, policy=policy, session_id=session_id)
        _webrtc_controllers[session_id] = ctrl

        if oidc_subject_hash and session_nonce:
            try:
                ctrl.start_session(oidc_subject_hash=oidc_subject_hash, session_nonce=session_nonce)
                msg = f"WebRTC session {session_id!r} starting (signaling: {signaling_url or 'not configured'})"
            except ValueError as exc:
                del _webrtc_controllers[session_id]
                msg = f"WebRTC session {session_id!r} not started: {exc}"
                return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        else:
            msg = f"WebRTC session {session_id!r} created (OIDC/nonce not set — call 'oidc login' first)"

        return CommandResult(state.with_updates(status_message=msg, command_line=""), msg)

    if command == "center.browser.webrtc.stop":
        ctrl = _webrtc_controllers.get(session_id)
        if ctrl is None:
            msg = f"WebRTC session {session_id!r} not active"
            return CommandResult(state.with_updates(status_message=msg), msg)
        ctrl.stop_session()  # type: ignore[union-attr]
        del _webrtc_controllers[session_id]
        msg = f"WebRTC session {session_id!r} stopped"
        return CommandResult(state.with_updates(status_message=msg, command_line=""), msg)

    if command == "center.browser.webrtc.status":
        ctrl = _webrtc_controllers.get(session_id)
        if ctrl is None:
            msg = "WebRTC session not active"
            return CommandResult(state.with_updates(status_message=msg), msg)
        status = ctrl.get_status()  # type: ignore[union-attr]
        parts = [
            f"auth={status.get('auth')}",
            f"signaling={status.get('signaling')}",
            f"ice={status.get('ice')}",
            f"dc={status.get('datachannel')}",
            f"peer={status.get('peer_id') or 'none'}",
            f"transfer={status.get('transfer_state')}",
        ]
        msg = "WebRTC: " + " | ".join(parts)
        return CommandResult(state.with_updates(status_message=msg), msg)

    if command == "center.browser.webrtc.accept_artifact":
        if not args:
            msg = "center.browser.webrtc.accept_artifact <offer_id> [session_id]"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        offer_id = args[0]
        ctrl = _webrtc_controllers.get(session_id)
        if ctrl is None:
            msg = "WebRTC session not active"
            return CommandResult(state.with_updates(status_message=msg), msg)
        try:
            ctrl.accept_artifact(offer_id)  # type: ignore[union-attr]
            msg = f"Artifact offer {offer_id!r} accepted"
        except ValueError as exc:
            msg = f"Artifact accept failed: {exc}"
        return CommandResult(state.with_updates(status_message=msg, command_line=""), msg)

    msg = f"unknown webrtc command: {command}"
    return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
