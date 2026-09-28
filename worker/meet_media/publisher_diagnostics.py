"""Opt-in live diagnostics for a publication browser (``MEET_PUBLISHER_DIAGNOSTICS=1``).

Records network, WebSocket, console and page errors of the machine session to a local
file and hooks join/publish failures -- for debugging a live Meet path, never needed for a
publication. Off by default: nothing is installed, and ``note_failure`` is a no-op.
"""

import json
import os

DIAGNOSTICS_ENV = "MEET_PUBLISHER_DIAGNOSTICS"
LOG_PATH = "/tmp/meet-turn.err"

_CAPTURE_SCRIPT = """(() => {
  const orig = window.fetch; window.__diag = {};
  window.fetch = async (input, init) => {
    const response = await orig(input, init);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (url.indexOf('/config') >= 0) response.clone().json().then(v => { window.__diag.cfg = v; }).catch(() => {});
      if (url.indexOf('/api/machine/sessions') >= 0 && (!init || (init.method || 'GET').toUpperCase() === 'POST')) {
        response.clone().json().then(v => { window.__diag.session = v; }).catch(() => {});
      }
    } catch (error) {}
    return response;
  };
})();"""
_CRYPTO_SCRIPT = """(() => {
  try {
    window.__diag = window.__diag || {};
    if (!window.crypto || !window.crypto.subtle) { window.__diag.crypto = 'no-subtle'; }
    else {
      crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveKey'])
        .then(() => { window.__diag.crypto = 'ok'; })
        .catch((error) => { window.__diag.crypto = 'genfail:' + String(error); });
    }
  } catch (error) { window.__diag = window.__diag || {}; window.__diag.crypto = 'throw:' + String(error); }
})();"""
_ERROR_HOOKS = """() => {
  const api = window.anantaMachine;
  const origJoin = api.join;
  api.join = (...args) => origJoin(...args).catch(error => {
    window.__joinError = String((error && error.message) || error);
    throw error;
  });
  const origPublish = api.publish;
  api.publish = (...args) => origPublish(...args).catch(error => {
    window.__publishError = String((error && error.message) || error);
    throw error;
  });
}"""
_FAILURE_PROBES = {
    "join": ("() => ({ error: window.__joinError, now: Date.now(),"
             " e2ee: window.__diag && window.__diag.cfg && window.__diag.cfg.mediaE2ee,"
             " session: window.__diag && window.__diag.session })"),
    "publish": "() => ({ error: window.__publishError, status: window.anantaMachine.status() })",
    "crypto": "() => window.__diag && window.__diag.crypto",
}


def enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(DIAGNOSTICS_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


class PublisherDiagnostics:
    """Live diagnostics of one publication browser; a no-op unless enabled."""

    def __init__(self, active, log_path=LOG_PATH):
        self.active = bool(active)
        self._log_path = log_path

    def install_context(self, context):
        if self.active:
            context.add_init_script(_CAPTURE_SCRIPT)
            context.add_init_script(_CRYPTO_SCRIPT)

    def install_page(self, page):
        if not self.active:
            return
        page.on("response", self._on_response)
        page.on("console", lambda message: self._write(f"CONSOLE {message.type} {message.text[:200]}"))
        page.on("pageerror", lambda error: self._write(f"PAGEERROR {str(error)[:200]}"))
        page.on("websocket", self._on_websocket)

    def hook_errors(self, page):
        if self.active:
            page.evaluate(_ERROR_HOOKS)

    def note_failure(self, page, phase, attempt=None):
        """Record page state after a failed phase; never raises, never replaces the failure."""
        if not self.active:
            return
        try:
            state = page.evaluate(_FAILURE_PROBES[phase])
            label = phase.upper() + ("" if attempt is None else f"#{attempt}")
            self._write(f"{label} {json.dumps(state, default=str)[:1200]}")
        except Exception:  # noqa: BLE001 - best-effort diagnostics only
            pass

    def _write(self, text):
        try:
            with open(self._log_path, "a") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    def _on_response(self, response):
        if "/api/machine/sessions" in response.url:
            try:
                self._write(f"NET {response.status} {response.url} {response.text()[:800]}")
            except Exception as error:  # noqa: BLE001 - diagnostic only
                self._write(f"NET {response.status} {response.url} <{error!r}>")
        elif "/api/machine" in response.url:
            self._write(f"NET {response.status} {response.url}")

    def _on_websocket(self, ws):
        self._write(f"WS {ws.url}")
        ws.on("socketerror", lambda error: self._write(f"WSERR {error}"))
        ws.on("close", lambda: self._write("WSCLOSE"))

        def on_frame(frame_type, payload):
            try:
                text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
                if any(marker in text for marker in ("overlay", "key-ack", "membership", "topology")):
                    self._write(f"WS{frame_type} {text[:400]}")
            except Exception as error:  # noqa: BLE001 - diagnostic only
                self._write(f"WS{frame_type} <{error!r}>")

        ws.on("framesent", lambda payload: on_frame("SENT", payload))
        ws.on("framereceived", lambda payload: on_frame("RECV", payload))
