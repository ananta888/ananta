"""SS05.01: TUI Snapshot/Delta Stream für Live-Share.

- Owner-TUI erzeugt periodisch initialen Snapshot und Deltas
- Empfänger rekonstruiert Snapshot + Deltas lokal
- Hash-Mismatch löst Resync per Full Snapshot aus
- Stream läuft nicht im Render-Hotpath
"""
from __future__ import annotations

import hashlib
import json
import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from client_surfaces.operator_tui.tui_snapshot import rendered_tui_snapshot_text
from client_surfaces.operator_tui.share_view_policy import (
    ViewSharePolicy,
    build_default_policy,
    check_and_redact_snapshot,
)
from client_surfaces.operator_tui.share_crypto import (
    encrypt_view,
    decrypt_view,
    DecryptionFailedError,
    SessionKeyPair,
)


_SNAPSHOT_INTERVAL = 5.0   # Sekunden zwischen initialen Snapshots
_DELTA_INTERVAL = 1.0      # Sekunden zwischen Delta-Checks
_MAX_PAYLOAD_BYTES = 256 * 1024  # 256 KB


@dataclass
class ViewFrame:
    kind: str  # "snapshot" | "delta"
    session_id: str
    message_id: str
    width: int
    height: int
    base_hash: str
    new_hash: str
    text: str  # Klartext (wird vor dem Versand verschlüsselt)
    sent_at: float = field(default_factory=time.time)

    def to_wire_dict(self, encrypted_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": "1",
            "session_id": self.session_id,
            "message_id": self.message_id,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "base_hash": self.base_hash,
            "new_hash": self.new_hash,
            "encrypted_payload": encrypted_payload,
            "sent_at": self.sent_at,
        }


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


class ViewStreamSender:
    """Betreibt den Owner-seitigen Snapshot/Delta-Stream."""

    def __init__(
        self,
        session_id: str,
        shared_key: bytes,
        policy: ViewSharePolicy | None = None,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._shared_key = shared_key
        self._policy = policy or build_default_policy()
        self._on_frame = on_frame
        self._last_snapshot_hash = ""
        self._last_snapshot_time = 0.0
        self._last_delta_time = 0.0
        self._lock = threading.Lock()
        self._active = False

    def start(self) -> None:
        with self._lock:
            self._active = True

    def stop(self) -> None:
        with self._lock:
            self._active = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def tick(self, rendered_text: str, *, width: int, height: int, now: float | None = None) -> None:
        """Wird aus dem Snake-Tick aufgerufen. Läuft nicht im Render-Hotpath."""
        if not self.is_active:
            return
        t = float(now if now is not None else time.time())
        decision, redacted = check_and_redact_snapshot(rendered_text, self._policy)
        if not decision.allowed:
            return
        new_hash = _text_hash(redacted)
        should_full = (t - self._last_snapshot_time >= _SNAPSHOT_INTERVAL) or not self._last_snapshot_hash
        if should_full or new_hash == self._last_snapshot_hash:
            if not should_full:
                return  # kein Delta nötig
        is_delta = bool(self._last_snapshot_hash) and not should_full
        frame = ViewFrame(
            kind="delta" if is_delta else "snapshot",
            session_id=self._session_id,
            message_id=str(uuid.uuid4()),
            width=width,
            height=height,
            base_hash=self._last_snapshot_hash,
            new_hash=new_hash,
            text=redacted,
        )
        self._last_snapshot_hash = new_hash
        if not is_delta:
            self._last_snapshot_time = t
        payload_bytes = redacted.encode()
        if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
            return  # zu groß
        try:
            encrypted = encrypt_view(payload_bytes, self._shared_key, frame.message_id)
            wire = frame.to_wire_dict(encrypted.to_dict())
            if self._on_frame:
                self._on_frame(wire)
        except Exception:
            pass


class ViewStreamReceiver:
    """Empfänger-Seite: rekonstruiert Snapshots aus Frames."""

    def __init__(self, shared_key: bytes) -> None:
        self._shared_key = shared_key
        self._current_text = ""
        self._current_hash = ""
        self._lock = threading.Lock()
        self._stale = False
        self._disconnected = False

    @property
    def current_text(self) -> str:
        with self._lock:
            return self._current_text

    @property
    def is_stale(self) -> bool:
        with self._lock:
            return self._stale

    def mark_disconnected(self) -> None:
        with self._lock:
            self._disconnected = True
            self._stale = True

    def handle_frame(self, wire: dict[str, Any]) -> bool:
        """Verarbeitet einen empfangenen Frame. Returns True bei Erfolg."""
        enc_dict = wire.get("encrypted_payload")
        if not enc_dict:
            return False
        from client_surfaces.operator_tui.share_crypto import EncryptedPayload
        try:
            payload = EncryptedPayload.from_dict(enc_dict)
            text_bytes = decrypt_view(payload, self._shared_key)
            new_text = text_bytes.decode()
        except DecryptionFailedError:
            return False
        except Exception:
            return False

        new_hash = _text_hash(new_text)
        base_hash = str(wire.get("base_hash") or "")
        kind = str(wire.get("kind") or "snapshot")

        with self._lock:
            if kind == "delta" and base_hash and base_hash != self._current_hash:
                self._stale = True
                return False  # Hash-Mismatch → Resync nötig
            self._current_text = new_text
            self._current_hash = new_hash
            self._stale = False
            self._disconnected = False
        return True

    def needs_resync(self) -> bool:
        with self._lock:
            return self._stale
