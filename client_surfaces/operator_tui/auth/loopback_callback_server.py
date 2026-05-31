"""Local loopback HTTP callback listener for OIDC authorization_code_pkce flows.

Binds to 127.0.0.1 on a random free port and accepts exactly ONE request
at /callback.  The server runs in a daemon thread and stops automatically
after the first callback or a configurable timeout.

Security invariants:
- Only binds to 127.0.0.1 (loopback).
- Rejects requests from non-loopback IPs.
- Returns minimal HTML pages — never embeds token values.
"""
from __future__ import annotations

import http.server
import queue
import socket
import threading
import urllib.parse
from typing import Optional


_SUCCESS_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Login successful</title></head>
<body>
<h2>Login successful</h2>
<p>You may close this tab and return to the Ananta TUI.</p>
</body>
</html>
"""

_ERROR_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Login failed</title></head>
<body>
<h2>Login failed</h2>
<p>{reason}</p>
<p>Please return to the Ananta TUI and try again.</p>
</body>
</html>
"""

_BIND_HOST = "127.0.0.1"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP request handler that captures the OIDC callback."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        # Only handle /callback path
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        # Reject non-loopback clients (defence in depth)
        client_ip = self.client_address[0]
        if client_ip not in ("127.0.0.1", "::1", "localhost"):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden: loopback only")
            return

        # Build result dict and put it on the queue
        result: dict[str, str] = {}
        if "code" in params:
            result["code"] = params["code"]
        if "state" in params:
            result["state"] = params["state"]
        if "error" in params:
            result["error"] = params["error"]
            result["error_description"] = params.get("error_description", "")

        # Respond to the browser
        if "error" in result:
            body = _ERROR_HTML.format(reason=result.get("error_description") or result["error"]).encode("utf-8")
            self.send_response(400)
        else:
            body = _SUCCESS_HTML.encode("utf-8")
            self.send_response(200)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        # Signal that callback was received
        server: LoopbackCallbackServer = self.server._callback_server  # type: ignore[attr-defined]
        server._result_queue.put(result)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        # Suppress default access log output (no token exposure)
        pass


class _CallbackHTTPServer(http.server.HTTPServer):
    """HTTPServer subclass that carries a reference to LoopbackCallbackServer."""

    _callback_server: "LoopbackCallbackServer"


class LoopbackCallbackServer:
    """Local loopback HTTP server that captures exactly one OIDC callback.

    Usage::

        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=180.0)
        # ... open authorization_url in Carbonyl ...
        result = server.wait_for_callback()  # blocks until callback or timeout
        server.stop()

    The ``result`` dict contains ``code`` and ``state`` keys on success,
    or an ``error`` key on provider error.
    """

    def __init__(self) -> None:
        self._httpd: Optional[_CallbackHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._result_queue: queue.Queue[dict] = queue.Queue(maxsize=1)
        self._redirect_uri: str = ""
        self._port: int = 0
        self._timeout: float = 180.0
        self._stopped = threading.Event()

    def start(self, timeout_seconds: float = 180.0) -> str:
        """Start the loopback listener and return the redirect URI.

        Binds to a random free port on 127.0.0.1.

        Args:
            timeout_seconds: How long to wait for the callback before
                giving up.  Default is 180 seconds.

        Returns:
            The redirect URI to register with the OIDC provider, e.g.
            ``http://127.0.0.1:54321/callback``.

        Raises:
            RuntimeError: If the server is already running.
        """
        if self._httpd is not None:
            raise RuntimeError("LoopbackCallbackServer is already running")

        self._timeout = timeout_seconds
        self._stopped.clear()

        # Bind to port 0 to get a random free port
        httpd = _CallbackHTTPServer((_BIND_HOST, 0), _CallbackHandler)
        httpd._callback_server = self
        self._httpd = httpd
        self._port = httpd.server_address[1]
        self._redirect_uri = f"http://{_BIND_HOST}:{self._port}/callback"

        self._thread = threading.Thread(
            target=self._serve_loop,
            daemon=True,
            name="LoopbackCallbackServer",
        )
        self._thread.start()
        return self._redirect_uri

    def wait_for_callback(self) -> dict:
        """Block until the callback is received or the timeout expires.

        Returns:
            Dict with keys: ``code`` and ``state`` on success,
            or ``error`` (and optionally ``error_description``) on failure.
            Returns ``{"error": "callback_timeout"}`` if the timeout expires.
        """
        try:
            result = self._result_queue.get(timeout=self._timeout)
        except queue.Empty:
            result = {"error": "callback_timeout"}
        finally:
            self._stopped.set()
            self.stop()
        return result

    def stop(self) -> None:
        """Stop the server and release the port."""
        self._stopped.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    @property
    def redirect_uri(self) -> str:
        """The redirect URI this server is listening on."""
        return self._redirect_uri

    @property
    def port(self) -> int:
        """The port this server is bound to."""
        return self._port

    def _serve_loop(self) -> None:
        """Internal thread target: serve until stopped or callback received."""
        if self._httpd is None:
            return
        try:
            while not self._stopped.is_set():
                self._httpd.handle_request()
                # If a result is on the queue, we're done
                if not self._result_queue.empty():
                    break
        except Exception:
            pass
        finally:
            self._stopped.set()
