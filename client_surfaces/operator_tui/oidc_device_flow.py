"""OIDC Device Authorization Grant (RFC 8628) für die Ananta TUI.

Flow:
  1. POST {issuer}/protocol/openid-connect/auth/device  → device_code, user_code, verification_uri
  2. TUI zeigt user_code an, User öffnet verification_uri im Browser
  3. Polling: POST {issuer}/protocol/openid-connect/token bis access_token kommt

Alles non-blocking: Polling läuft im Hintergrund-Thread, Ergebnis landet in game state.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceFlowState:
    status: str           # "starting" | "waiting" | "polling" | "done" | "error" | "expired"
    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    verification_uri_complete: str = ""
    expires_at: float = 0.0
    interval: float = 5.0
    access_token: str = ""
    error: str = ""
    issuer: str = ""
    client_id: str = ""


def start_device_flow(issuer: str, client_id: str) -> DeviceFlowState:
    """Startet Device Flow synchron (einmaliger POST). Wirft bei Fehler."""
    url = f"{issuer.rstrip('/')}/protocol/openid-connect/auth/device"
    body = urllib.parse.urlencode({"client_id": client_id}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"Device flow start failed ({exc.code}): {body_text}") from exc
    except Exception as exc:
        raise RuntimeError(f"Device flow start failed: {exc}") from exc

    return DeviceFlowState(
        status="waiting",
        device_code=str(data.get("device_code") or ""),
        user_code=str(data.get("user_code") or ""),
        verification_uri=str(data.get("verification_uri") or ""),
        verification_uri_complete=str(data.get("verification_uri_complete") or data.get("verification_uri") or ""),
        expires_at=time.time() + float(data.get("expires_in") or 600),
        interval=max(3.0, float(data.get("interval") or 5.0)),
        issuer=issuer,
        client_id=client_id,
    )


def poll_device_flow(state: DeviceFlowState) -> DeviceFlowState:
    """Ein Poll-Versuch. Gibt neuen State zurück."""
    if state.status not in ("waiting", "polling"):
        return state
    if time.time() >= state.expires_at:
        return DeviceFlowState(**{**state.__dict__, "status": "expired", "error": "Device code expired"})

    url = f"{state.issuer.rstrip('/')}/protocol/openid-connect/token"
    body = urllib.parse.urlencode({
        "client_id": state.client_id,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": state.device_code,
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        token = str(data.get("access_token") or "").strip()
        if token:
            return DeviceFlowState(**{**state.__dict__, "status": "done", "access_token": token})
        return DeviceFlowState(**{**state.__dict__, "status": "polling", "error": ""})
    except urllib.error.HTTPError as exc:
        try:
            err_data = json.loads(exc.read())
            error = str(err_data.get("error") or "")
        except Exception:
            error = str(exc)
        if error == "authorization_pending":
            return DeviceFlowState(**{**state.__dict__, "status": "polling", "error": ""})
        if error == "slow_down":
            return DeviceFlowState(**{**state.__dict__, "status": "polling", "interval": state.interval + 2})
        if error in ("access_denied", "expired_token"):
            return DeviceFlowState(**{**state.__dict__, "status": "error", "error": error})
        return DeviceFlowState(**{**state.__dict__, "status": "polling", "error": error})
    except Exception as exc:
        return DeviceFlowState(**{**state.__dict__, "status": "polling", "error": str(exc)})


class DeviceFlowPoller:
    """Pollt den Device Flow im Hintergrund. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: DeviceFlowState | None = None
        self._last_poll: float = 0.0
        self._thread: threading.Thread | None = None

    def start(self, issuer: str, client_id: str) -> DeviceFlowState:
        state = start_device_flow(issuer, client_id)
        with self._lock:
            self._state = state
            # 0.0 sorgt dafür, dass der erste tick() sofort pollt,
            # unabhängig davon ob now time.monotonic() oder time.time() ist.
            self._last_poll = 0.0
        return state

    def tick(self, now: float) -> DeviceFlowState | None:
        """Nicht-blockierender Tick. Gibt aktuellen State zurück."""
        with self._lock:
            state = self._state
        if state is None or state.status in ("done", "error", "expired"):
            return state
        if now - self._last_poll < state.interval:
            return state
        self._last_poll = now
        t = threading.Thread(target=self._do_poll, daemon=True)
        t.start()
        return state

    def _do_poll(self) -> None:
        with self._lock:
            state = self._state
        if state is None:
            return
        new_state = poll_device_flow(state)
        with self._lock:
            self._state = new_state

    def get_state(self) -> DeviceFlowState | None:
        with self._lock:
            return self._state

    def clear(self) -> None:
        with self._lock:
            self._state = None


# Globaler Sidecar-Poller (gesetzt von :oidc login, konsumiert vom Tick)
_active_poller: DeviceFlowPoller | None = None


def status_lines(state: DeviceFlowState, *, width: int = 60) -> list[str]:
    """Rendert den Device Flow Status für die TUI."""
    lines: list[str] = []
    if state.status == "waiting":
        lines.append("  \x1b[1mOIDC Login:\x1b[0m Browser öffnen und Code eingeben:")
        lines.append("")
        lines.append(f"  URL: \x1b[36m{state.verification_uri}\x1b[0m")
        lines.append(f"  Code: \x1b[1;33m{state.user_code}\x1b[0m")
        lines.append("")
        lines.append("  Warte auf Browser-Login…")
    elif state.status == "polling":
        lines.append("  \x1b[33m⟳ Warte auf Browser-Bestätigung…\x1b[0m")
        lines.append(f"  Code: \x1b[1;33m{state.user_code}\x1b[0m")
        if state.error:
            lines.append(f"  \x1b[90m({state.error})\x1b[0m")
    elif state.status == "done":
        lines.append("  \x1b[32m✓ OIDC Login erfolgreich!\x1b[0m")
    elif state.status == "expired":
        lines.append("  \x1b[31m✗ Code abgelaufen. :oidc login erneut starten.\x1b[0m")
    elif state.status == "error":
        lines.append(f"  \x1b[31m✗ Login fehlgeschlagen: {state.error}\x1b[0m")
        lines.append("  :oidc login erneut versuchen")
    return lines
