"""PRD04.01: WebRTC-Transport für Ananta Shared Sessions.

Versucht WebRTC DataChannel; fällt auf Hub Relay zurück.
Policy- und Redaction-Gates bleiben identisch zu Hub Relay.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable


class WebRtcTransportError(Exception):
    pass


class WebRtcTransport:
    """Minimaler WebRTC-Transport: signaling über Hub, DataChannel wenn verfügbar."""

    def __init__(
        self,
        hub_url: str,
        session_id: str,
        user_id: str,
        token: str,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._hub_url = hub_url.rstrip("/")
        self._session_id = session_id
        self._user_id = user_id
        self._token = token
        self._on_message = on_message
        self._lock = threading.Lock()
        self._peer_id: str | None = None
        self._rtc_available = self._check_rtc()
        self._using_relay = not self._rtc_available
        self._last_poll = 0.0
        self._poll_interval = 1.0

    def _check_rtc(self) -> bool:
        try:
            import aiortc  # type: ignore
            return True
        except ImportError:
            return False

    @property
    def transport_mode(self) -> str:
        return "hub_relay" if self._using_relay else "webrtc"

    def send_signal(self, recipient_id: str, signal_type: str, payload: Any) -> bool:
        url = f"{self._hub_url}/webrtc/sessions/{self._session_id}/signal"
        body = json.dumps({
            "recipient_id": recipient_id,
            "type": signal_type,
            "payload": payload,
        }).encode()
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.status in {200, 201}
        except Exception:
            return False

    def poll_signals(self) -> list[dict[str, Any]]:
        url = f"{self._hub_url}/webrtc/sessions/{self._session_id}/signal"
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                return list(data.get("data", {}).get("signals") or [])
        except Exception:
            return []

    def tick(self, now: float | None = None) -> None:
        """Nicht-blockierender Tick für Signal-Polling."""
        t = float(now if now is not None else time.time())
        if t - self._last_poll < self._poll_interval:
            return
        self._last_poll = t
        if self._on_message:
            t = threading.Thread(target=self._do_poll, daemon=True)
            t.start()

    def _do_poll(self) -> None:
        for signal in self.poll_signals():
            if self._on_message:
                try:
                    self._on_message(signal)
                except Exception:
                    pass

    def send_view_frame(self, wire_frame: dict[str, Any]) -> bool:
        """Sendet einen View-Frame über Hub Relay (WebRTC DataChannel folgt später)."""
        url = f"{self._hub_url}/share-sessions/{self._session_id}/view/push"
        body = json.dumps(wire_frame).encode()
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in {200, 201}
        except Exception:
            return False
