import json
import logging
import os
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import jwt

from agent.config import settings
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.terminal_bridge import build_terminal_bridge

try:
    from flask_sock import Sock
except ImportError:  # pragma: no cover - optional dependency for minimal installs
    Sock = None  # type: ignore[assignment]


LOGGER = logging.getLogger("agent.ws_terminal")
_TERMINAL_IO_TIMEOUT_SECONDS = 0.05


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
                    return jwt.decode(provided_token, agent_token, algorithms=["HS256"], leeway=30)
                except jwt.PyJWTError:
                    pass
            return jwt.decode(provided_token, settings.secret_key, algorithms=["HS256"], leeway=30)
        if agent_token and provided_token == agent_token:
            return {"sub": "agent_token", "role": "admin"}
    except jwt.PyJWTError:
        return None
    except Exception:
        return None

    return None


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


def register_ws_terminal(app: Any) -> None:
    if Sock is None:
        LOGGER.warning("flask-sock not installed, /ws/terminal endpoint disabled")
        return

    sock = Sock(app)

    @sock.route("/ws/terminal")
    def ws_terminal(ws: Any):
        session_id = f"ws-{uuid.uuid4()}"
        environ, provided_token, mode, forward_param = _extract_ws_context(ws)
        mode = mode if mode in {"interactive", "read"} else "interactive"
        agent_token = app.config.get("AGENT_TOKEN")
        data_dir = app.config.get("DATA_DIR", settings.data_dir)
        remote_addr = environ.get("REMOTE_ADDR")
        auth_payload = _decode_token(provided_token or "", agent_token)

        if agent_token and not auth_payload:
            _send_event(ws, "error", {"message": "unauthorized"})
            return

        principal = (auth_payload or {}).get("sub") or "anonymous"
        _append_terminal_log(
            data_dir,
            {
                "timestamp": time.time(),
                "timestamp_iso": _utc_now_iso(),
                "session_id": session_id,
                "event": "session_open",
                "mode": mode,
                "principal": principal,
                "forward_param": forward_param,
                "remote_addr": remote_addr,
            },
        )

        _send_event(
            ws,
            "ready",
            {
                "session_id": session_id,
                "mode": mode,
                "read_only": mode == "read",
            },
        )

        if mode == "read":
            log_path = Path(data_dir) / "terminal_log.jsonl"
            for line in _tail_lines(log_path):
                _send_event(ws, "output", {"chunk": line})

            # Follow file updates like `tail -f`.
            file_pos = log_path.stat().st_size if log_path.exists() else 0
            while True:
                try:
                    _ = _recv_message(ws, timeout_seconds=0.5)
                except Exception as exc:
                    if _is_timeout_error(exc):
                        pass
                    else:
                        break

                if not log_path.exists():
                    continue

                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as fh:
                        fh.seek(file_pos)
                        fresh = fh.read()
                        file_pos = fh.tell()
                    if fresh:
                        _send_event(ws, "output", {"chunk": fresh})
                except Exception:
                    continue

            _append_terminal_log(
                data_dir,
                {
                    "timestamp": time.time(),
                    "timestamp_iso": _utc_now_iso(),
                    "session_id": session_id,
                    "event": "session_close",
                    "mode": mode,
                    "principal": principal,
                },
            )
            return

        forwarded_terminal = None
        if forward_param:
            forwarded_terminal = get_live_terminal_session_service().get_session(forward_param)
            if forwarded_terminal is None:
                _send_event(ws, "error", {"message": "forward_terminal_not_found"})
                _append_terminal_log(
                    data_dir,
                    {
                        "timestamp": time.time(),
                        "timestamp_iso": _utc_now_iso(),
                        "session_id": session_id,
                        "event": "session_close",
                        "mode": mode,
                        "principal": principal,
                        "error": "forward_terminal_not_found",
                    },
                )
                return
        if forwarded_terminal is not None:
            offset = 0
            chunks, offset = get_live_terminal_session_service().read_from(forward_param, offset)
            if chunks:
                _send_event(ws, "output", {"chunk": "".join(chunks)})
            try:
                while True:
                    try:
                        incoming = _recv_message(ws, timeout_seconds=_TERMINAL_IO_TIMEOUT_SECONDS)
                    except Exception as exc:
                        if _is_timeout_error(exc):
                            incoming = None
                        elif _is_closed_error(exc):
                            break
                        else:
                            LOGGER.debug("Transient websocket receive error in %s: %s", session_id, exc)
                            incoming = None
                    if incoming is not None:
                        resize = _extract_terminal_resize(incoming)
                        if resize is not None:
                            get_live_terminal_session_service().resize(forward_param, resize[0], resize[1])
                            continue
                        text = _extract_terminal_input(incoming)
                        if isinstance(text, str) and text:
                            get_live_terminal_session_service().write(forward_param, text)
                    changed = get_live_terminal_session_service().wait_for_update(
                        forward_param,
                        offset,
                        _TERMINAL_IO_TIMEOUT_SECONDS,
                    )
                    if changed:
                        fresh, offset = get_live_terminal_session_service().read_from(forward_param, offset)
                        if fresh:
                            _send_event(ws, "output", {"chunk": "".join(fresh)})
            finally:
                _append_terminal_log(
                    data_dir,
                    {
                        "timestamp": time.time(),
                        "timestamp_iso": _utc_now_iso(),
                        "session_id": session_id,
                        "event": "session_close",
                        "mode": mode,
                        "principal": principal,
                        "forward_param": forward_param,
                    },
                )
            return

        bridge = build_terminal_bridge(_safe_shell())
        try:
            bridge.start()
        except RuntimeError as exc:
            _send_event(ws, "error", {"message": str(exc)})
            _append_terminal_log(
                data_dir,
                {
                    "timestamp": time.time(),
                    "timestamp_iso": _utc_now_iso(),
                    "session_id": session_id,
                    "event": "session_close",
                    "mode": mode,
                    "principal": principal,
                    "error": str(exc),
                },
            )
            return

        try:
            while True:
                for chunk in bridge.drain():
                    _send_event(ws, "output", {"chunk": chunk})

                try:
                    incoming = _recv_message(ws, timeout_seconds=_TERMINAL_IO_TIMEOUT_SECONDS)
                except Exception as exc:
                    if _is_timeout_error(exc):
                        continue
                    if _is_closed_error(exc):
                        break
                    # Some websocket stacks raise transient receive errors while the
                    # socket is still usable. Keep the stream alive in that case.
                    LOGGER.debug("Transient websocket receive error in %s: %s", session_id, exc)
                    continue

                if incoming is None:
                    # In some websocket backends, `None` can mean "no message
                    # available right now" instead of a hard disconnect.
                    continue

                resize = _extract_terminal_resize(incoming)
                if resize is not None:
                    resize_fn = getattr(bridge, "resize", None)
                    if callable(resize_fn):
                        resize_fn(resize[0], resize[1])
                    continue

                text = _extract_terminal_input(incoming)
                if not text:
                    continue

                bridge.write(text)
                if text.strip():
                    try:
                        preview = " ".join(shlex.split(text))[:120]
                    except ValueError:
                        preview = text.strip().replace("\n", " ")[:120]
                else:
                    preview = ""
                _append_terminal_log(
                    data_dir,
                    {
                        "timestamp": time.time(),
                        "timestamp_iso": _utc_now_iso(),
                        "session_id": session_id,
                        "event": "input",
                        "mode": mode,
                        "principal": principal,
                        "preview": preview,
                    },
                )
        except Exception as exc:
            LOGGER.exception("Terminal websocket session %s aborted: %s", session_id, exc)
        finally:
            bridge.close()
            _append_terminal_log(
                data_dir,
                {
                    "timestamp": time.time(),
                    "timestamp_iso": _utc_now_iso(),
                    "session_id": session_id,
                    "event": "session_close",
                    "mode": mode,
                    "principal": principal,
                },
            )
