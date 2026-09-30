import json
import logging
import os
import re
import shlex
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs

import jwt

from agent.auth import (
    AgentTokenConfigurationError,
    authenticate_provided_token,
    get_request_auth_context,
    resolve_configured_agent_token,
)
from agent.config import settings
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.platform_governance_service import get_platform_governance_service
from agent.services.terminal_bridge import build_terminal_bridge
from agent.services.user_token_scope import token_scope_allows_request

try:
    from flask_sock import Sock
except ImportError:  # pragma: no cover - optional dependency for minimal installs
    Sock = None  # type: ignore[assignment]


LOGGER = logging.getLogger("agent.ws_terminal")
_TERMINAL_IO_TIMEOUT_SECONDS = 0.05
_TERMINAL_OUTPUT_WAIT_SECONDS = 0.25
_WS_INPUT_PUMP_TIMEOUT_SECONDS = 0.25


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_shell() -> str:
    if os.name == "nt":
        candidates = [
            settings.shell_path,
            os.environ.get("COMSPEC"),
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "powershell.exe",
            "cmd.exe",
        ]
        for shell in candidates:
            if shell and (os.path.exists(shell) or "\\" not in shell):
                return shell
        return "cmd.exe"

    shell = settings.shell_path or "/bin/sh"
    return shell if os.path.exists(shell) else "/bin/sh"


def _decode_token(provided_token: str, agent_token: str | None) -> dict[str, Any] | None:
    if not provided_token:
        return None

    try:
        if provided_token.count(".") == 2:
            if agent_token:
                try:
                    payload = jwt.decode(provided_token, agent_token, algorithms=["HS256"], leeway=30)
                    return (
                        payload
                        if token_scope_allows_request(
                            payload,
                            method="GET",
                            path="/ws/terminal",
                        )
                        else None
                    )
                except jwt.PyJWTError:
                    pass
            payload = jwt.decode(provided_token, settings.secret_key, algorithms=["HS256"], leeway=30)
            return (
                payload
                if token_scope_allows_request(
                    payload,
                    method="GET",
                    path="/ws/terminal",
                )
                else None
            )
        if agent_token and provided_token == agent_token:
            return {"sub": "agent_token", "role": "admin"}
    except jwt.PyJWTError:
        return None
    except Exception:
        return None

    return None


def _auth_payload_is_admin(auth_payload: dict[str, Any] | None) -> bool:
    payload = auth_payload if isinstance(auth_payload, dict) else {}
    role = str(payload.get("role") or "").strip().lower()
    roles = payload.get("roles") if isinstance(payload.get("roles"), list) else []
    return role == "admin" or "admin" in {str(item or "").strip().lower() for item in roles}


def _auth_payload_roles(auth_payload: dict[str, Any] | None) -> list[str]:
    payload = auth_payload if isinstance(auth_payload, dict) else {}
    roles = []
    role = str(payload.get("role") or "").strip()
    if role:
        roles.append(role)
    raw_roles = payload.get("roles") if isinstance(payload.get("roles"), list) else []
    for item in raw_roles:
        candidate = str(item or "").strip()
        if candidate:
            roles.append(candidate)
    return roles


def _terminal_limit_reason(
    *,
    policy: dict[str, Any],
    started_at: float,
    last_activity_at: float,
    now: float | None = None,
) -> str | None:
    current = time.monotonic() if now is None else now
    max_session_seconds = int(policy.get("max_session_seconds") or 0)
    idle_timeout_seconds = int(policy.get("idle_timeout_seconds") or 0)
    if max_session_seconds > 0 and current - started_at >= max_session_seconds:
        return "terminal_max_session_seconds_exceeded"
    if idle_timeout_seconds > 0 and current - last_activity_at >= idle_timeout_seconds:
        return "terminal_idle_timeout_seconds_exceeded"
    return None


def _terminal_preview_limit(policy: dict[str, Any]) -> int:
    try:
        return max(0, int(policy.get("input_preview_max_chars", 120)))
    except (TypeError, ValueError):
        return 120


def _extract_ws_context(ws: Any) -> tuple[dict[str, Any], str | None, str, str | None]:
    environ = getattr(ws, "environ", {}) or {}
    query = parse_qs(environ.get("QUERY_STRING", ""))
    auth_header = environ.get("HTTP_AUTHORIZATION") or ""
    query_token = (query.get("token") or [None])[0]
    provided_token = query_token

    if auth_header.startswith("Bearer "):
        provided_token = auth_header.split(" ", 1)[1]

    mode = (query.get("mode") or ["interactive"])[0]
    forward_param = (query.get("forward_param") or [None])[0]

    return environ, provided_token, mode or "interactive", forward_param


def _ws_token_came_from_query(environ: dict[str, Any]) -> bool:
    auth_header = str(environ.get("HTTP_AUTHORIZATION") or "")
    query = parse_qs(str(environ.get("QUERY_STRING") or ""))
    return not auth_header.startswith("Bearer ") and bool((query.get("token") or [None])[0])


def _authenticate_terminal_token(
    provided_token: str | None,
    *,
    app_config: Mapping[str, Any],
    token_from_query: bool = False,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Authenticate a terminal credential through the central Hub boundary.

    Returns ``(payload, reason_or_mode, auth_required)``. File-managed service
    credentials are deliberately header-only; signed user credentials still
    pass through the central scope policy, which rejects stream derivatives on
    the terminal path.
    """

    file_managed = bool(
        str(app_config.get("AGENT_TOKEN_FILE") or os.environ.get("AGENT_TOKEN_FILE") or "").strip()
    )
    try:
        configured_agent_token = resolve_configured_agent_token(app_config)
    except AgentTokenConfigurationError:
        LOGGER.error("Terminal WebSocket rejected an invalid AGENT_TOKEN_FILE configuration")
        return None, "agent_token_file_invalid", True

    auth_required = configured_agent_token is not None
    if file_managed and token_from_query:
        return None, "agent_token_query_forbidden", True
    authenticated, auth_mode = authenticate_provided_token(
        provided_token,
        require_admin=False,
    )
    if not authenticated:
        return None, auth_mode or "invalid_token", auth_required
    if auth_mode == "auth_disabled":
        return None, auth_mode, False

    auth_payload = get_request_auth_context()
    if auth_mode == "agent_static_token" and not auth_payload:
        auth_payload = {
            "sub": "agent_token",
            "role": "admin",
            "auth_mode": auth_mode,
        }
    return auth_payload or None, auth_mode, auth_required


def _append_terminal_log(data_dir: str, entry: dict[str, Any]) -> None:
    try:
        os.makedirs(data_dir, exist_ok=True)
        log_path = Path(data_dir) / "terminal_log.jsonl"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - logging fallback
        LOGGER.warning("Failed to append terminal log: %s", exc)


def _tail_lines(path: Path, limit: int = 100) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        return lines[-limit:]
    except Exception:
        return []


def _send_event(ws: Any, event_type: str, data: dict[str, Any] | None = None) -> None:
    payload = {"type": event_type, "data": data or {}}
    ws.send(json.dumps(payload))


def _extract_terminal_input(data: Any) -> str | None:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")
    if not isinstance(data, str):
        return None

    stripped = data.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return data
        if isinstance(payload, dict):
            if payload.get("type") == "input":
                return str(payload.get("data", ""))
            return None
    return data


def _extract_terminal_resize(data: Any) -> tuple[int, int] | None:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")
    if not isinstance(data, str):
        return None

    stripped = data.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "resize":
        return None
    try:
        cols = max(1, int(payload.get("cols") or 0))
        rows = max(1, int(payload.get("rows") or 0))
    except (TypeError, ValueError):
        return None
    return cols, rows


def _recv_message(ws: Any, timeout_seconds: float = 0.2) -> Any:
    try:
        return ws.receive(timeout=timeout_seconds)
    except TypeError:
        return ws.receive()


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if "timeout" in exc.__class__.__name__.lower():
        return True
    return "timed out" in str(exc).lower()


def _is_closed_error(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return "closed" in text or "disconnect" in text


class _WebSocketInputPump:
    def __init__(self, ws: Any, on_message: Any) -> None:
        self._ws = ws
        self._on_message = on_message
        self._stop = threading.Event()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                incoming = _recv_message(self._ws, timeout_seconds=_WS_INPUT_PUMP_TIMEOUT_SECONDS)
            except Exception as exc:
                if _is_timeout_error(exc):
                    continue
                if _is_closed_error(exc):
                    self._closed.set()
                    return
                LOGGER.debug("Transient websocket receive error: %s", exc)
                continue
            if incoming is None:
                continue
            try:
                self._on_message(incoming)
            except Exception:
                LOGGER.debug("Failed to process websocket terminal input", exc_info=True)
        self._closed.set()


def _attach_token_ws_session(ws: Any, token: str, app: Any) -> None:
    """Handle WSS connection authenticated via a short-lived attach token."""
    from agent.db_models import TerminalEventDB
    from agent.services.repository_registry import get_repository_registry
    from agent.services.terminal_session_service import get_terminal_session_service
    from agent.services.tmux_backend import TmuxBackendError, get_tmux_session_backend
    from agent.services.terminal_recording_service import redact_secrets

    svc = get_terminal_session_service()
    resolved = svc.resolve_attach_token(token)
    if resolved is None:
        _send_event(ws, "error", {"message": "unauthorized", "details": "invalid_or_expired_attach_token"})
        return

    session_id, user_id = resolved
    registry = get_repository_registry()
    entry = registry.terminal_session_repo.get_by_id(session_id)
    if entry is None or entry.status not in {"running", "detached"}:
        _send_event(ws, "error", {"message": "terminal_session_not_active"})
        return

    tmux_name = str(entry.tmux_session_name or "")
    read_only = bool(entry.read_only)
    backend = get_tmux_session_backend()

    registry.terminal_event_repo.append(
        TerminalEventDB(
            session_id=session_id,
            user_id=user_id,
            event_type="session_attached",
            target_type=entry.target_type,
            target_id=entry.target_id,
            operation="attach",
            allowed=True,
            reason_code="terminal_gateway_attach",
            summary="WSS attach via token",
        )
    )
    registry.terminal_session_repo.transition_status(session_id, "attached")

    _send_event(
        ws,
        "ready",
        {
            "session_id": session_id,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "read_only": read_only,
            "tmux_session": tmux_name,
        },
    )

    started_at = time.monotonic()
    last_activity = started_at
    data_dir = app.config.get("DATA_DIR", settings.data_dir)
    max_lifetime = int(settings.terminal_max_lifetime_seconds or 14400)
    idle_timeout = int(settings.terminal_idle_timeout_seconds or 900)

    def _handle_input(incoming: Any) -> None:
        nonlocal last_activity
        if read_only:
            return
        text = _extract_terminal_input(incoming)
        if not text:
            return
        try:
            backend.send_input(session_name=tmux_name, text=text)
            last_activity = time.monotonic()
        except TmuxBackendError as exc:
            LOGGER.debug("tmux send_input failed for %s: %s", session_id, exc)

    pump = _WebSocketInputPump(ws, _handle_input)
    pump.start()
    try:
        while True:
            time.sleep(_TERMINAL_IO_TIMEOUT_SECONDS)
            now = time.monotonic()
            if max_lifetime > 0 and now - started_at >= max_lifetime:
                _send_event(ws, "error", {"message": "terminal_session_closed", "details": "terminal_max_lifetime_exceeded"})
                break
            if idle_timeout > 0 and now - last_activity >= idle_timeout:
                _send_event(ws, "error", {"message": "terminal_session_closed", "details": "terminal_idle_timeout_exceeded"})
                break
            try:
                raw = backend.capture_output(session_name=tmux_name, lines=50)
            except TmuxBackendError:
                _send_event(ws, "error", {"message": "terminal_session_closed", "details": "tmux_gone"})
                break
            if raw.strip():
                chunk = redact_secrets(raw)
                _send_event(ws, "output", {"chunk": chunk})
                last_activity = now
            if pump.closed:
                break
    except Exception as exc:
        LOGGER.exception("attach-token WSS session %s error: %s", session_id, exc)
    finally:
        pump.close()
        registry.terminal_session_repo.transition_status(session_id, "detached")
        registry.terminal_event_repo.append(
            TerminalEventDB(
                session_id=session_id,
                user_id=user_id,
                event_type="gateway_disconnected",
                target_type=entry.target_type,
                target_id=entry.target_id,
                operation="attach",
                allowed=True,
                reason_code="terminal_gateway_disconnect",
                summary="WSS gateway disconnected",
            )
        )
        _append_terminal_log(
            data_dir,
            {
                "timestamp": time.time(),
                "timestamp_iso": _utc_now_iso(),
                "session_id": session_id,
                "event": "gateway_disconnected",
                "user_id": user_id,
                "target_type": entry.target_type,
            },
        )


class _InputPreviewLog:
    """Buffer terminal input and write one audited preview line per entered command."""

    _MAX_BUFFER_CHARS = 4096
    _PARTIAL_TAIL_CHARS = 256

    def __init__(self, session: "_TerminalWsSession") -> None:
        self._session = session
        self._buffer = ""

    def _append(self, raw_line: str, *, partial: bool = False) -> None:
        candidate = str(raw_line or "").strip()
        if not candidate:
            return
        limit = _terminal_preview_limit(self._session.policy)
        try:
            preview = " ".join(shlex.split(candidate))[:limit]
        except ValueError:
            preview = candidate.replace("\n", " ").replace("\r", " ")[:limit]
        extra: dict[str, Any] = {"preview": preview}
        if partial:
            extra["partial"] = True
        self._session.log("input", **extra)

    def feed(self, text: str) -> None:
        self._buffer += text
        self.flush(force=False)

    def flush(self, *, force: bool = False) -> None:
        while True:
            match = re.search(r"[\r\n]", self._buffer)
            if not match:
                break
            line = self._buffer[: match.start()]
            self._buffer = self._buffer[match.end():]
            self._append(line, partial=False)
        if force and self._buffer.strip():
            self._append(self._buffer, partial=True)
            self._buffer = ""
        elif not force and len(self._buffer) > self._MAX_BUFFER_CHARS:
            self._append(self._buffer[-self._PARTIAL_TAIL_CHARS:], partial=True)
            self._buffer = ""


class _TerminalWsSession:
    """One ``/ws/terminal`` connection: authorization, audit log and the three session modes."""

    def __init__(self, ws: Any, app: Any) -> None:
        self.ws = ws
        self.app = app
        self.session_id = f"ws-{uuid.uuid4()}"
        environ, self.provided_token, mode, self.forward_param = _extract_ws_context(ws)
        self.environ = environ
        self.mode = mode if mode in {"interactive", "read"} else "interactive"
        self.data_dir = app.config.get("DATA_DIR", settings.data_dir)
        self.remote_addr = environ.get("REMOTE_ADDR")
        self.principal = "anonymous"
        self.policy: dict[str, Any] = {}
        self.started_at = time.monotonic()
        self.last_activity_at = self.started_at

    # ── audit log and limits ────────────────────────────────────────────────

    def log(self, event: str, **fields: Any) -> None:
        _append_terminal_log(
            self.data_dir,
            {
                "timestamp": time.time(),
                "timestamp_iso": _utc_now_iso(),
                "session_id": self.session_id,
                "event": event,
                "mode": self.mode,
                "principal": self.principal,
                **fields,
            },
        )

    def touch(self) -> None:
        self.last_activity_at = time.monotonic()

    def limit_reason(self) -> str | None:
        return _terminal_limit_reason(
            policy=self.policy,
            started_at=self.started_at,
            last_activity_at=self.last_activity_at,
        )

    def send_limit_if_needed(self) -> str | None:
        reason = self.limit_reason()
        if reason:
            _send_event(self.ws, "error", {"message": "terminal_session_closed", "details": reason})
        return reason

    # ── lifecycle ────────────────────────────────────────────────────────────

    def authorize(self) -> bool:
        """Authenticate the token and evaluate the terminal policy; reports rejections."""
        auth_payload, auth_reason, auth_required = _authenticate_terminal_token(
            self.provided_token,
            app_config=self.app.config,
            token_from_query=_ws_token_came_from_query(self.environ),
        )
        if (auth_required or self.provided_token) and not auth_payload and auth_reason != "auth_disabled":
            _send_event(self.ws, "error", {"message": "unauthorized", "details": auth_reason or "invalid_token"})
            return False
        self.principal = (auth_payload or {}).get("sub") or "anonymous"
        decision = get_platform_governance_service().evaluate_terminal_access(
            cfg=self.app.config.get("AGENT_CONFIG", {}) or {},
            terminal_mode=self.mode,
            is_admin=_auth_payload_is_admin(auth_payload),
            is_authenticated=bool(auth_payload),
            roles=_auth_payload_roles(auth_payload),
            remote_addr=self.remote_addr,
        )
        if decision.allowed:
            self.policy = decision.policy
            return True
        _send_event(
            self.ws,
            "error",
            {
                "message": "forbidden",
                "details": decision.reason,
                "platform_mode": decision.platform_mode,
                "mode": decision.mode,
            },
        )
        if decision.policy.get("emit_audit_events", True):
            self.log(
                "session_blocked",
                reason=decision.reason,
                platform_mode=decision.platform_mode,
                remote_addr=self.remote_addr,
            )
        return False

    def serve(self) -> None:
        if not self.authorize():
            return
        self.started_at = time.monotonic()
        self.last_activity_at = self.started_at
        self.log("session_open", forward_param=self.forward_param, remote_addr=self.remote_addr)
        _send_event(
            self.ws,
            "ready",
            {"session_id": self.session_id, "mode": self.mode, "read_only": self.mode == "read"},
        )
        if self.mode == "read":
            self.follow_terminal_log()
            return
        if self.forward_param:
            self.forward_live_terminal()
            return
        self.run_shell()

    # ── read mode ────────────────────────────────────────────────────────────

    def _receive_keepalive(self) -> bool:
        """Consume one client message; ``False`` when the connection is gone."""
        try:
            if _recv_message(self.ws, timeout_seconds=0.5) is not None:
                self.touch()
        except Exception as exc:
            return _is_timeout_error(exc)
        return True

    def _send_appended_log(self, log_path: Path, file_pos: int) -> tuple[int, bool]:
        """Send what was appended since ``file_pos``; returns ``(new_pos, ok)``."""
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(file_pos)
                fresh = fh.read()
                file_pos = fh.tell()
            if fresh:
                self.touch()
                _send_event(self.ws, "output", {"chunk": fresh})
        except Exception:
            return file_pos, False
        return file_pos, True

    def follow_terminal_log(self) -> None:
        """Stream the terminal audit log like ``tail -f``."""
        log_path = Path(self.data_dir) / "terminal_log.jsonl"
        for line in _tail_lines(log_path):
            _send_event(self.ws, "output", {"chunk": line})
        file_pos = log_path.stat().st_size if log_path.exists() else 0
        while self._receive_keepalive():
            if log_path.exists():
                file_pos, sent = self._send_appended_log(log_path, file_pos)
                if not sent:
                    continue
            if self.send_limit_if_needed():
                break
        self.log("session_close", reason=self.limit_reason())

    # ── forwarded live terminal ──────────────────────────────────────────────

    def forward_live_terminal(self) -> None:
        live_terminals = get_live_terminal_session_service()
        forward_param = self.forward_param
        if live_terminals.get_session(forward_param) is None:
            _send_event(self.ws, "error", {"message": "forward_terminal_not_found"})
            self.log("session_close", error="forward_terminal_not_found")
            return
        chunks, offset = live_terminals.read_from(forward_param, 0)
        if chunks:
            _send_event(self.ws, "output", {"chunk": "".join(chunks)})

        def _handle_forwarded_input(incoming: Any) -> None:
            resize = _extract_terminal_resize(incoming)
            if resize is not None:
                self.touch()
                get_live_terminal_session_service().resize(forward_param, resize[0], resize[1])
                return
            text = _extract_terminal_input(incoming)
            if isinstance(text, str) and text:
                self.touch()
                get_live_terminal_session_service().write(forward_param, text)

        pump = _WebSocketInputPump(self.ws, _handle_forwarded_input)
        pump.start()
        try:
            while True:
                if get_live_terminal_session_service().wait_for_update(
                    forward_param, offset, _TERMINAL_OUTPUT_WAIT_SECONDS
                ):
                    fresh, offset = get_live_terminal_session_service().read_from(forward_param, offset)
                    if fresh:
                        self.touch()
                        _send_event(self.ws, "output", {"chunk": "".join(fresh)})
                if pump.closed or self.send_limit_if_needed():
                    break
        finally:
            pump.close()
            self.log("session_close", forward_param=forward_param, reason=self.limit_reason())

    # ── local shell ──────────────────────────────────────────────────────────

    def run_shell(self) -> None:
        bridge = build_terminal_bridge(_safe_shell())
        try:
            bridge.start()
        except RuntimeError as exc:
            _send_event(self.ws, "error", {"message": str(exc)})
            self.log("session_close", error=str(exc))
            return
        preview_log = _InputPreviewLog(self)
        pump: _WebSocketInputPump | None = None
        try:
            pump = _WebSocketInputPump(
                self.ws, lambda incoming: self._handle_shell_input(bridge, preview_log, incoming)
            )
            pump.start()
            self._pump_shell_output(bridge, pump)
        except Exception as exc:
            LOGGER.exception("Terminal websocket session %s aborted: %s", self.session_id, exc)
        finally:
            if pump is not None:
                pump.close()
            preview_log.flush(force=True)
            bridge.close()
            self.log("session_close", reason=self.limit_reason())

    def _handle_shell_input(self, bridge: Any, preview_log: _InputPreviewLog, incoming: Any) -> None:
        resize = _extract_terminal_resize(incoming)
        if resize is not None:
            self.touch()
            resize_fn = getattr(bridge, "resize", None)
            if callable(resize_fn):
                resize_fn(resize[0], resize[1])
            return
        text = _extract_terminal_input(incoming)
        if not text:
            return
        bridge.write(text)
        self.touch()
        preview_log.feed(text)

    def _pump_shell_output(self, bridge: Any, pump: _WebSocketInputPump) -> None:
        while True:
            wait_for_output = getattr(bridge, "wait_for_output", None)
            if callable(wait_for_output):
                wait_for_output(_TERMINAL_OUTPUT_WAIT_SECONDS)
            else:
                time.sleep(_TERMINAL_IO_TIMEOUT_SECONDS)
            chunks = bridge.drain()
            if chunks:
                self.touch()
                _send_event(self.ws, "output", {"chunk": "".join(chunks)})
            if pump.closed or self.send_limit_if_needed():
                break


def register_ws_terminal(app: Any) -> None:
    if Sock is None:
        LOGGER.warning("flask-sock not installed, /ws/terminal endpoint disabled")
        return

    sock = Sock(app)

    @sock.route("/ws/terminal/session")
    def ws_terminal_session(ws: Any):
        """Attach-token-authenticated WSS endpoint for tmux-backed sessions."""
        environ = getattr(ws, "environ", {}) or {}
        query = parse_qs(environ.get("QUERY_STRING", ""))
        attach_token = (query.get("attach_token") or [None])[0]
        if not attach_token:
            _send_event(ws, "error", {"message": "unauthorized", "details": "attach_token_required"})
            return
        _attach_token_ws_session(ws, attach_token, app)

    @sock.route("/ws/terminal")
    def ws_terminal(ws: Any):
        _TerminalWsSession(ws, app).serve()
