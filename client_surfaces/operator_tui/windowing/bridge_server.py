from __future__ import annotations

import json
import threading
import uuid
import time
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
        self._state_lock = threading.Lock()
        self._state_payload: dict[str, Any] = {"schema_version": "window.bridge.v1", "state_version": "init", "payload": {}}
        self._events: deque[WindowActionEvent] = deque(maxlen=256)
        self._dropped_events = 0
        self._rejected_actions = 0
        self._accepted_actions = 0
        self._session_token = uuid.uuid4().hex
        self._recent_event_ids: deque[str] = deque(maxlen=512)
        self._event_timestamps: deque[float] = deque(maxlen=256)
        self._rate_limit_per_sec = 30.0

    @property
    def session_token(self) -> str:
        return self._session_token

    def start(self) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _cors_origin(self) -> str | None:
                origin = self.headers.get("Origin", "")
                for prefix in ("http://127.0.0.1:", "http://localhost:", "http://[::1]:"):
                    if origin.startswith(prefix):
                        return origin
                return None

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
                self.send_header("Content-Length", str(len(raw)))
                self._add_cors_headers()
                self.end_headers()
                self.wfile.write(raw)

            def _html(self, html: str) -> None:
                raw = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
                return token == server._session_token

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
                        self._json(403, {"ok": False, "error": "forbidden", "reason_code": "window_bridge_non_local_client"})
                        return
                    if not self._authorized():
                        self._json(401, {"ok": False, "error": "unauthorized", "reason_code": "window_bridge_unauthorized"})
                        return
                    with server._state_lock:
                        payload = dict(server._state_payload)
                    self._json(200, {"ok": True, "state": payload, "allowed_actions": allowed_actions()})
                    return
                if parsed.path == "/window":
                    token = server._session_token
                    self._html(_window_html(token))
                    return
                self._json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/action":
                    self._json(404, {"ok": False, "error": "not_found"})
                    return
                if not self._is_local_client():
                    self._json(403, {"ok": False, "error": "forbidden", "reason_code": "window_bridge_non_local_client"})
                    return
                if not self._authorized():
                    self._json(401, {"ok": False, "error": "unauthorized", "reason_code": "window_bridge_unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                body = self.rfile.read(max(0, length))
                try:
                    payload = json.loads(body.decode("utf-8") if body else "{}")
                except json.JSONDecodeError:
                    self._json(400, {"ok": False, "error": "invalid_json"})
                    return
                action_id = str(payload.get("action_id") or "").strip()
                args = dict(payload.get("args") or {}) if isinstance(payload.get("args"), dict) else {}
                event_id = str(payload.get("event_id") or uuid.uuid4().hex)
                now = time.monotonic()
                while server._event_timestamps and now - server._event_timestamps[0] > 1.0:
                    server._event_timestamps.popleft()
                if len(server._event_timestamps) >= int(server._rate_limit_per_sec):
                    server._rejected_actions += 1
                    self._json(429, {"ok": False, "error": "rate_limited", "reason_code": "window_bridge_rate_limited"})
                    return
                if not is_allowed_action(action_id):
                    server._rejected_actions += 1
                    self._json(
                        403,
                        {
                            "ok": False,
                            "error": "action_not_allowed",
                            "reason_code": "window_bridge_action_not_allowed",
                            "action_id": action_id,
                        },
                    )
                    return
                if event_id in server._recent_event_ids:
                    server._rejected_actions += 1
                    self._json(
                        409,
                        {
                            "ok": False,
                            "error": "duplicate_event",
                            "reason_code": "window_bridge_duplicate_event",
                            "event_id": event_id,
                        },
                    )
                    return
                if len(server._events) >= server._events.maxlen:
                    server._dropped_events += 1
                server._events.append(WindowActionEvent(action_id=action_id, args=args, event_id=event_id))
                server._event_timestamps.append(now)
                server._recent_event_ids.append(event_id)
                server._accepted_actions += 1
                self._json(202, {"ok": True, "accepted": action_id})

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                _ = (format, args)
                return

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="external-window-bridge")
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    def publish_state(self, payload: dict[str, Any]) -> None:
        with self._state_lock:
            self._state_payload = {
                "schema_version": "window.bridge.v1",
                "state_version": str(payload.get("state_version") or ""),
                "payload": payload,
            }

    def drain_events(self) -> list[WindowActionEvent]:
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
