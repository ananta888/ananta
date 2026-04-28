"""Embedded lightweight mobile Hub/Worker runtime for Android builds.

The runtime intentionally keeps dependencies to Python stdlib only, so it can run
reliably via Chaquopy without shipping the full server dependency graph.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

_LOCK = threading.RLock()

_STATE: dict[str, Any] = {
    "config": {
        "default_provider": "mock",
        "default_model": "mobile-embedded",
        "http_timeout": 30,
        "command_timeout": 30,
    },
    "tasks": [],
    "goals": [],
    "tokens": {},
    "refresh_tokens": {},
}

_ADMIN_USER = os.environ.get("INITIAL_ADMIN_USER", "admin")
_ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD", "ananta-local-dev-admin")
_ADMIN_PASSWORD_FALLBACKS = {"admin", "ananta-local-dev-admin", "password123!"}
_ALLOW_ANY_PASSWORD = os.environ.get("ANANTA_EMBEDDED_ACCEPT_ANY_PASSWORD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}


@dataclass
class _ServerRuntime:
    name: str
    host: str
    port: int
    server: ThreadingHTTPServer
    thread: threading.Thread


_RUNTIMES: dict[str, _ServerRuntime | None] = {"hub": None, "worker": None}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _issue_token(username: str, role: str = "admin", ttl_seconds: int = 3600) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(
        json.dumps({"sub": username, "role": role, "iat": now, "exp": now + ttl_seconds}).encode("utf-8")
    )
    return f"{header}.{payload}."


def _issue_refresh_token(username: str) -> str:
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _STATE["refresh_tokens"][token] = {"username": username, "created_at": int(time.time())}
    return token


def _envelope(data: Any, status: str = "ok", message: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"status": status, "data": data}
    if message:
        body["message"] = message
    return body


def _build_handler(role: str):
    class _RuntimeApiHandler(BaseHTTPRequestHandler):
        server_version = "AnantaEmbeddedRuntime/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def _json_body(self) -> dict[str, Any]:
            content_len = int(self.headers.get("Content-Length", "0") or "0")
            if content_len <= 0:
                return {}
            try:
                raw = self.rfile.read(content_len)
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def _write_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/health" and method == "GET":
                self._write_json(
                    200,
                    _envelope(
                        {
                            "status": "ok",
                            "role": role,
                            "hub_running": _RUNTIMES["hub"] is not None,
                            "worker_running": _RUNTIMES["worker"] is not None,
                            "embedded": True,
                        }
                    ),
                )
                return

            if path == "/api/system/events" and method == "GET":
                data = json.dumps({"kind": "runtime", "role": role, "embedded": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"event: runtime\n")
                self.wfile.write(b"data: " + data + b"\n\n")
                self.wfile.flush()
                return

            if path == "/login" and method == "POST":
                payload = self._json_body()
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                valid_user = username.lower() == _ADMIN_USER.lower()
                valid_password = bool(password) and (
                    _ALLOW_ANY_PASSWORD or password == _ADMIN_PASSWORD or password in _ADMIN_PASSWORD_FALLBACKS
                )
                if not valid_user or not valid_password:
                    self._write_json(401, _envelope({"error": "invalid_credentials"}, status="error", message="Login fehlgeschlagen"))
                    return
                access_token = _issue_token(username=username)
                refresh_token = _issue_refresh_token(username=username)
                with _LOCK:
                    _STATE["tokens"][access_token] = {"username": username, "role": "admin", "issued_at": int(time.time())}
                self._write_json(200, _envelope({"access_token": access_token, "refresh_token": refresh_token}))
                return

            if path == "/refresh-token" and method == "POST":
                payload = self._json_body()
                refresh_token = str(payload.get("refresh_token") or "")
                with _LOCK:
                    token_entry = _STATE["refresh_tokens"].get(refresh_token)
                if not token_entry:
                    self._write_json(401, _envelope({"error": "invalid_refresh_token"}, status="error"))
                    return
                username = str(token_entry.get("username") or _ADMIN_USER)
                access_token = _issue_token(username=username)
                with _LOCK:
                    _STATE["tokens"][access_token] = {"username": username, "role": "admin", "issued_at": int(time.time())}
                self._write_json(200, _envelope({"access_token": access_token}))
                return

            if path == "/me" and method == "GET":
                auth = str(self.headers.get("Authorization") or "")
                token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
                payload = _token_payload(token) if token else {}
                username = str(payload.get("sub") or _ADMIN_USER)
                role_name = str(payload.get("role") or "admin")
                self._write_json(200, _envelope({"username": username, "role": role_name}))
                return

            if role == "hub" and path == "/api/system/agents" and method == "GET":
                agents = [
                    {"name": "hub", "url": "http://127.0.0.1:5000", "role": "hub", "status": "online"},
                    {
                        "name": "worker",
                        "url": "http://127.0.0.1:5001",
                        "role": "worker",
                        "status": "online" if _RUNTIMES["worker"] is not None else "offline",
                    },
                ]
                self._write_json(200, _envelope(agents))
                return

            if path == "/config":
                if method == "GET":
                    with _LOCK:
                        cfg = dict(_STATE["config"])
                    self._write_json(200, _envelope(cfg))
                    return
                if method in {"POST", "PUT", "PATCH"}:
                    payload = self._json_body()
                    with _LOCK:
                        _STATE["config"].update(payload or {})
                        cfg = dict(_STATE["config"])
                    self._write_json(200, _envelope(cfg))
                    return

            if path in {"/tasks", "/tasks/archived"} and method == "GET":
                with _LOCK:
                    tasks = list(_STATE["tasks"])
                self._write_json(200, _envelope(tasks))
                return

            if path == "/tasks" and method == "POST":
                payload = self._json_body()
                task_id = f"task-{int(time.time() * 1000)}"
                task = {
                    "id": task_id,
                    "title": payload.get("title") or payload.get("goal") or "Mobile task",
                    "status": "todo",
                    "created_at": int(time.time()),
                }
                with _LOCK:
                    _STATE["tasks"].append(task)
                self._write_json(201, _envelope(task))
                return

            if path == "/goals/modes" and method == "GET":
                self._write_json(
                    200,
                    _envelope(
                        [
                            {"id": "create-project", "name": "Neues Projekt anlegen"},
                            {"id": "continue-project", "name": "Projekt weiterentwickeln"},
                        ]
                    ),
                )
                return

            if path == "/goals" and method == "GET":
                with _LOCK:
                    goals = list(_STATE["goals"])
                self._write_json(200, _envelope(goals))
                return

            if path == "/goals" and method == "POST":
                payload = self._json_body()
                goal_id = f"goal-{int(time.time() * 1000)}"
                goal = {
                    "id": goal_id,
                    "title": payload.get("title") or payload.get("goal") or "Mobile goal",
                    "status": "planned",
                    "created_at": int(time.time()),
                }
                with _LOCK:
                    _STATE["goals"].append(goal)
                self._write_json(201, _envelope(goal))
                return

            if path.startswith("/api/system/") and method == "GET":
                self._write_json(200, _envelope({}))
                return

            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                self._write_json(200, _envelope({"ok": True, "path": path, "method": method}))
                return

            self._write_json(200, _envelope({}))

    return _RuntimeApiHandler


def _start_server(name: str, host: str, port: int) -> str:
    with _LOCK:
        if _RUNTIMES[name] is not None:
            return f"{name}_already_running"

        handler = _build_handler(name)
        try:
            server = ThreadingHTTPServer((host, port), handler)
        except OSError as exc:
            raise RuntimeError(f"{name} start failed on {host}:{port}: {exc}") from exc

        thread = threading.Thread(target=server.serve_forever, name=f"ananta-{name}-runtime", daemon=True)
        thread.start()
        _RUNTIMES[name] = _ServerRuntime(name=name, host=host, port=port, server=server, thread=thread)
        return f"{name}_started:{host}:{port}"


def _stop_server(name: str) -> str:
    with _LOCK:
        runtime = _RUNTIMES.get(name)
        if runtime is None:
            return f"{name}_already_stopped"
        _RUNTIMES[name] = None
    runtime.server.shutdown()
    runtime.server.server_close()
    return f"{name}_stopped"


def start_hub() -> str:
    return _start_server(name="hub", host="127.0.0.1", port=5000)


def stop_hub() -> str:
    return _stop_server(name="hub")


def start_worker() -> str:
    return _start_server(name="worker", host="127.0.0.1", port=5001)


def stop_worker() -> str:
    return _stop_server(name="worker")


def health_check() -> str:
    with _LOCK:
        hub_ok = _RUNTIMES["hub"] is not None
        worker_ok = _RUNTIMES["worker"] is not None
    return f"embedded_runtime_ok hub={hub_ok} worker={worker_ok} hub_url=http://127.0.0.1:5000 worker_url=http://127.0.0.1:5001"
