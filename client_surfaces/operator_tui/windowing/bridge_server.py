from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from client_surfaces.operator_tui.windowing.protocol import WindowActionEvent, allowed_actions, is_allowed_action


@dataclass(frozen=True)
class BridgeStatus:
    running: bool
    host: str
    port: int
    dropped_events: int
    rejected_actions: int
    accepted_actions: int


class ExternalWindowBridgeServer:
    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = int(port)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._closing = False
        self._state_lock = threading.Lock()
        self._state_payload: dict[str, Any] = {
            "schema_version": "window.bridge.v1",
            "state_version": "init",
            "payload": {},
        }
        self._auth_context: dict[str, str] | None = None
        self._auth_context_expires_at = 0.0
        self._action_lock = threading.Lock()
        self._action_session_token = ""
        self._action_legacy_token = ""
        self._events: deque[WindowActionEvent] = deque(maxlen=256)
        self._dropped_events = 0
        self._rejected_actions = 0
        self._accepted_actions = 0
        self._session_token = ""
        self._legacy_window_token = ""
        self._allowed_browser_origins: set[str] = set()
        self._recent_event_ids: deque[str] = deque(maxlen=512)
        self._event_timestamps: deque[float] = deque(maxlen=256)
        self._rate_limit_per_sec = 30.0

    @property
    def session_token(self) -> str:
        return self._session_token

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._httpd is not None:
                return
            self._closing = False
            session_token = uuid.uuid4().hex
            legacy_window_token = uuid.uuid4().hex
            with self._state_lock:
                self._session_token = session_token
                self._legacy_window_token = legacy_window_token
            with self._action_lock:
                self._action_session_token = session_token
                self._action_legacy_token = legacy_window_token
                self._events.clear()
                self._recent_event_ids.clear()
                self._event_timestamps.clear()
            server = self

            class Handler(BaseHTTPRequestHandler):
                def _cors_origin(self) -> str | None:
                    origin = self.headers.get("Origin", "")
                    return origin if origin in server._allowed_browser_origins else None

                def _add_cors_headers(self) -> None:
                    cors = self._cors_origin()
                    if cors:
                        self.send_header("Access-Control-Allow-Origin", cors)
                        self.send_header("Access-Control-Allow-Headers", "X-Ananta-Window-Token, Content-Type")
                        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

                def _json(self, code: int, payload: dict[str, Any]) -> None:
                    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(raw)))
                    self._add_cors_headers()
                    self.end_headers()
                    self.wfile.write(raw)

                def _html(self, html: str) -> None:
                    raw = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                    )
                    self.send_header("Content-Length", str(len(raw)))
                    self._add_cors_headers()
                    self.end_headers()
                    self.wfile.write(raw)

                def do_OPTIONS(self) -> None:  # noqa: N802
                    cors = self._cors_origin()
                    if not cors or not self._is_local_client():
                        self.send_response(403)
                        self.end_headers()
                        return
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", cors)
                    self.send_header("Access-Control-Allow-Headers", "X-Ananta-Window-Token, Content-Type")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Max-Age", "86400")
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def _authorized(self) -> bool:
                    token = self.headers.get("X-Ananta-Window-Token", "")
                    return server.action_token_is_current(token)

                def _is_local_client(self) -> bool:
                    host = str((self.client_address or ("", 0))[0] or "")
                    return host in {"127.0.0.1", "::1", "localhost"}

                def do_GET(self) -> None:  # noqa: N802
                    parsed = urlparse(self.path)
                    if parsed.path == "/health":
                        self._json(200, {"ok": True, "status": server.status().__dict__})
                        return
                    if parsed.path == "/state":
                        if not self._is_local_client():
                            self._json(
                                403,
                                {
                                    "ok": False,
                                    "error": "forbidden",
                                    "reason_code": "window_bridge_non_local_client",
                                },
                            )
                            return
                        payload = server.state_for_token(self.headers.get("X-Ananta-Window-Token", ""))
                        if payload is None:
                            self._json(
                                401,
                                {
                                    "ok": False,
                                    "error": "unauthorized",
                                    "reason_code": "window_bridge_unauthorized",
                                },
                            )
                            return
                        self._json(200, {"ok": True, "state": payload, "allowed_actions": allowed_actions()})
                        return
                    if parsed.path == "/auth-context":
                        if not self._is_local_client() or not self._cors_origin():
                            self._json(
                                403,
                                {
                                    "ok": False,
                                    "error": "forbidden",
                                    "reason_code": "window_bridge_non_local_client",
                                },
                            )
                            return
                        auth_status, auth_context = server.consume_auth_context(
                            self.headers.get("X-Ananta-Window-Token", "")
                        )
                        if auth_status == "unauthorized":
                            self._json(
                                401,
                                {
                                    "ok": False,
                                    "error": "unauthorized",
                                    "reason_code": "window_bridge_unauthorized",
                                },
                            )
                            return
                        if auth_status != "ok" or auth_context is None:
                            self._json(
                                410,
                                {
                                    "ok": False,
                                    "error": "auth_context_consumed",
                                    "reason_code": "window_bridge_auth_context_consumed",
                                },
                            )
                            return
                        self._json(200, {"ok": True, "auth": auth_context})
                        return
                    if parsed.path == "/window":
                        self._html(_window_html(server._legacy_window_token))
                        return
                    self._json(404, {"ok": False, "error": "not_found"})

                def do_POST(self) -> None:  # noqa: N802
                    parsed = urlparse(self.path)
                    if parsed.path != "/action":
                        self._json(404, {"ok": False, "error": "not_found"})
                        return
                    if not self._is_local_client():
                        self._json(
                            403,
                            {
                                "ok": False,
                                "error": "forbidden",
                                "reason_code": "window_bridge_non_local_client",
                            },
                        )
                        return
                    if not self._authorized():
                        self._json(
                            401,
                            {
                                "ok": False,
                                "error": "unauthorized",
                                "reason_code": "window_bridge_unauthorized",
                            },
                        )
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if length < 0 or length > 64 * 1024:
                        self._json(
                            413,
                            {
                                "ok": False,
                                "error": "action_too_large",
                                "reason_code": "window_bridge_action_too_large",
                            },
                        )
                        return
                    body = self.rfile.read(max(0, length))
                    try:
                        payload = json.loads(body.decode("utf-8") if body else "{}")
                    except json.JSONDecodeError:
                        self._json(400, {"ok": False, "error": "invalid_json"})
                        return
                    action_id = str(payload.get("action_id") or "").strip()
                    raw_args = payload.get("args")
                    args = dict(raw_args) if isinstance(raw_args, dict) else {}
                    event_id = str(payload.get("event_id") or uuid.uuid4().hex)
                    code, result = server.enqueue_action(
                        action_id=action_id,
                        args=args,
                        event_id=event_id,
                        token=self.headers.get("X-Ananta-Window-Token", ""),
                    )
                    self._json(code, result)

                def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                    _ = (format, args)

            self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
            self._port = int(self._httpd.server_address[1])
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                daemon=True,
                name="external-window-bridge",
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            httpd = self._httpd
            self._closing = True
            self._httpd = None
            self._thread = None
            with self._state_lock:
                self._auth_context = None
                self._auth_context_expires_at = 0.0
                self._session_token = ""
                self._legacy_window_token = ""
            with self._action_lock:
                self._action_session_token = ""
                self._action_legacy_token = ""
                self._events.clear()
                self._recent_event_ids.clear()
                self._event_timestamps.clear()
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()

    def publish_auth_context(self, context: dict[str, str]) -> None:
        """Stage credentials for one authenticated loopback retrieval."""
        allowed = {"hub_url", "hub_token", "oidc_token"}
        if set(context) - allowed:
            raise ValueError("window_bridge_auth_context_fields_invalid")
        normalized: dict[str, str] = {}
        for key in allowed:
            value = context.get(key, "")
            if not isinstance(value, str) or len(value) > 16_384:
                raise ValueError("window_bridge_auth_context_value_invalid")
            normalized[key] = value
        if normalized["hub_token"] and not normalized["hub_url"]:
            raise ValueError("window_bridge_hub_authority_required")
        with self._state_lock:
            if not self._session_token or self._closing:
                raise RuntimeError("window_bridge_not_running")
            self._auth_context = normalized
            self._auth_context_expires_at = time.monotonic() + 60.0

    def set_allowed_browser_origin(self, value: str) -> None:
        """Pin CORS to the exact loopback Angular origin before startup."""
        if self._httpd is not None:
            raise RuntimeError("window_bridge_origin_already_bound")
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("window_bridge_origin_invalid")
        self._allowed_browser_origins = {f"{parsed.scheme}://{parsed.netloc}"}

    def consume_auth_context(self, token: str) -> tuple[str, dict[str, str] | None]:
        """Authorize, return and erase one context under the same lifecycle lock."""
        with self._state_lock:
            if self._closing or not token or token != self._session_token:
                return "unauthorized", None
            if self._auth_context is None or time.monotonic() > self._auth_context_expires_at:
                self._auth_context = None
                self._auth_context_expires_at = 0.0
                return "unavailable", None
            context = dict(self._auth_context)
            self._auth_context = None
            self._auth_context_expires_at = 0.0
            return "ok", context

    def state_for_token(self, token: str) -> dict[str, Any] | None:
        """Return a state snapshot only for the currently active capability."""
        with self._state_lock:
            if self._closing or token not in {self._session_token, self._legacy_window_token}:
                return None
            return dict(self._state_payload)

    def action_token_is_current(self, token: str) -> bool:
        with self._action_lock:
            return (
                bool(token)
                and not self._closing
                and token
                in {
                    self._action_session_token,
                    self._action_legacy_token,
                }
            )

    def publish_state(self, payload: dict[str, Any]) -> None:
        with self._state_lock:
            self._state_payload = {
                "schema_version": "window.bridge.v1",
                "state_version": str(payload.get("state_version") or ""),
                "payload": payload,
            }

    def enqueue_action(
        self,
        *,
        action_id: str,
        args: dict[str, Any],
        event_id: str,
        token: str,
    ) -> tuple[int, dict[str, Any]]:
        """Validate and enqueue one action atomically across request threads."""
        now = time.monotonic()
        with self._action_lock:
            if self._closing or token not in {self._action_session_token, self._action_legacy_token}:
                return 401, {
                    "ok": False,
                    "error": "unauthorized",
                    "reason_code": "window_bridge_unauthorized",
                }
            while self._event_timestamps and now - self._event_timestamps[0] > 1.0:
                self._event_timestamps.popleft()
            if len(self._event_timestamps) >= int(self._rate_limit_per_sec):
                self._rejected_actions += 1
                return 429, {
                    "ok": False,
                    "error": "rate_limited",
                    "reason_code": "window_bridge_rate_limited",
                }
            if not is_allowed_action(action_id):
                self._rejected_actions += 1
                return 403, {
                    "ok": False,
                    "error": "action_not_allowed",
                    "reason_code": "window_bridge_action_not_allowed",
                    "action_id": action_id,
                }
            if event_id in self._recent_event_ids:
                self._rejected_actions += 1
                return 409, {
                    "ok": False,
                    "error": "duplicate_event",
                    "reason_code": "window_bridge_duplicate_event",
                    "event_id": event_id,
                }
            if len(self._events) >= self._events.maxlen:
                self._dropped_events += 1
            self._events.append(WindowActionEvent(action_id=action_id, args=args, event_id=event_id))
            self._event_timestamps.append(now)
            self._recent_event_ids.append(event_id)
            self._accepted_actions += 1
            return 202, {"ok": True, "accepted": action_id}

    def drain_events(self) -> list[WindowActionEvent]:
        with self._action_lock:
            items = list(self._events)
            self._events.clear()
            return items

    def window_url(self) -> str:
        return f"http://{self._host}:{self._port}/window"

    def status(self) -> BridgeStatus:
        return BridgeStatus(
            running=self._httpd is not None,
            host=self._host,
            port=self._port,
            dropped_events=self._dropped_events,
            rejected_actions=self._rejected_actions,
            accepted_actions=self._accepted_actions,
        )


def _window_html(token: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ananta External Window</title>
<style>
body{{font-family:ui-monospace,Menlo,Consolas,monospace;margin:0;background:#0d1424;color:#e4ecff;padding:14px}}
.row{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}} button{{padding:6px 10px;background:#1f3155;color:#e4ecff;border:1px solid #38517f;border-radius:8px;cursor:pointer}}
pre{{white-space:pre-wrap;background:#111a30;border:1px solid #31476d;border-radius:10px;padding:10px}}
</style></head>
<body>
<div class="row">
<button onclick="act('snake.pause')">Snake Pause</button>
<button onclick="act('snake.resume')">Snake Resume</button>
<button onclick="act('view.next')">View Next</button>
<button onclick="act('view.previous')">View Prev</button>
<button onclick="act('view.simple')">View Simple</button>
<button onclick="act('view.doc')">View Doc</button>
<button onclick="act('view.snake')">View Snake</button>
</div>
<pre id="out">loading...</pre>
<script>
const TOKEN = {json.dumps(token)};
async function state() {{
  const r = await fetch('/state', {{headers: {{'X-Ananta-Window-Token': TOKEN}}}});
  const j = await r.json();
  document.getElementById('out').textContent = JSON.stringify(j, null, 2);
}}
async function act(actionId) {{
  await fetch('/action', {{
    method:'POST',
    headers: {{'Content-Type':'application/json','X-Ananta-Window-Token': TOKEN}},
    body: JSON.stringify({{action_id: actionId, args: {{}}, event_id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now())}})
  }});
  await state();
}}
setInterval(state, 700);
state();
</script>
</body></html>"""
