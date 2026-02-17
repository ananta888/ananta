import json
import logging
import os
import pty
import queue
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import jwt

from agent.config import settings

try:
    from flask_sock import Sock
except ImportError:  # pragma: no cover - optional dependency for minimal installs
    Sock = None  # type: ignore[assignment]


LOGGER = logging.getLogger("agent.ws_terminal")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_shell() -> str:
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


@dataclass
class PtyBridge:
    shell: str

    def __post_init__(self) -> None:
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        self.process = subprocess.Popen(  # noqa: S603
            [self.shell],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        if self.master_fd is None:
            return
        while not self._stop.is_set():
            try:
                chunk = os.read(self.master_fd, 4096)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="ignore")
                try:
                    self.output_queue.put_nowait(decoded)
                except queue.Full:
                    _ = self.output_queue.get_nowait()
                    self.output_queue.put_nowait(decoded)
            except OSError:
                break

    def write(self, data: str) -> None:
        if self.master_fd is None:
            return
        os.write(self.master_fd, data.encode("utf-8", errors="ignore"))

    def drain(self) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    def close(self) -> None:
        self._stop.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


def _send_event(ws: Any, event_type: str, data: dict[str, Any] | None = None) -> None:
    payload = {"type": event_type, "data": data or {}}
    ws.send(json.dumps(payload))


def _recv_message(ws: Any, timeout_seconds: float = 0.2) -> Any:
    try:
        return ws.receive(timeout=timeout_seconds)
    except TypeError:
        return ws.receive()


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return "timeout" in exc.__class__.__name__.lower()


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

        bridge = PtyBridge(shell=_safe_shell())
        bridge.start()

        try:
            while True:
                for chunk in bridge.drain():
                    _send_event(ws, "output", {"chunk": chunk})

                try:
                    incoming = _recv_message(ws, timeout_seconds=0.2)
                except Exception as exc:
                    if _is_timeout_error(exc):
                        continue
                    break

                if incoming is None:
                    break

                data = incoming
                if isinstance(incoming, bytes):
                    data = incoming.decode("utf-8", errors="ignore")

                if isinstance(data, str):
                    stripped = data.strip()
                    payload: dict[str, Any] | None = None
                    if stripped.startswith("{"):
                        try:
                            payload = json.loads(stripped)
                        except json.JSONDecodeError:
                            payload = None

                    if payload and payload.get("type") == "input":
                        text = str(payload.get("data", ""))
                    else:
                        text = data

                    if text:
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
