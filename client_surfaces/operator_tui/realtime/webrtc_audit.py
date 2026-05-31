"""WebRTC session audit log for Ananta DataChannel stack.

Security invariants:
- Artifact payloads are NEVER logged.
- Raw tokens, cookies, and credentials are NEVER logged.
- Full ICE candidate IPs are NEVER logged.
- JWT-like strings (starting with "eyJ") are NEVER logged.
- In-memory ring buffer: max 200 entries.
"""
from __future__ import annotations

import collections
import re
import threading
import time
from dataclasses import dataclass, field


_TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Event types
EVENT_SESSION_START = "session_start"
EVENT_SIGNALING_CONNECTED = "signaling_connected"
EVENT_PEER_OFFER_RECEIVED = "peer_offer_received"
EVENT_PEER_ACCEPTED = "peer_accepted"
EVENT_DATACHANNEL_OPEN = "datachannel_open"
EVENT_ARTIFACT_OFFER = "artifact_offer"
EVENT_ARTIFACT_COMPLETE = "artifact_complete"
EVENT_SESSION_CLOSED = "session_closed"
EVENT_ERROR = "error"

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_SESSION_START,
        EVENT_SIGNALING_CONNECTED,
        EVENT_PEER_OFFER_RECEIVED,
        EVENT_PEER_ACCEPTED,
        EVENT_DATACHANNEL_OPEN,
        EVENT_ARTIFACT_OFFER,
        EVENT_ARTIFACT_COMPLETE,
        EVENT_SESSION_CLOSED,
        EVENT_ERROR,
    }
)

_RING_MAX = 200


def _scrub(text: str) -> str:
    """Remove token-like strings and IP addresses from a string."""
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _IP_RE.sub("[redacted_ip]", text)
    return text


@dataclass
class WebRtcAuditEvent:
    """A single audit event for a WebRTC session.

    Attributes
    ----------
    event_type : str
        One of ALLOWED_EVENT_TYPES.
    session_id : str
        Session identifier (not secret).
    peer_id_hash : str
        Hashed peer ID — never the raw peer ID if it encodes identity.
    error_category : str
        Short error category string, e.g. "signaling_timeout". Empty on success.
    timestamp : float
        UTC seconds since epoch.
    """

    event_type: str
    session_id: str = ""
    peer_id_hash: str = ""
    error_category: str = ""
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        ts = round(self.timestamp, 3)
        parts = [
            f"[WebRtcAudit] {self.event_type}",
            f"session={self.session_id}",
            f"peer_hash={self.peer_id_hash}",
        ]
        if self.error_category:
            parts.append(f"error={self.error_category}")
        parts.append(f"ts={ts}")
        return " ".join(parts)

    def __repr__(self) -> str:
        return self.__str__()


class WebRtcAuditLog:
    """Thread-safe in-memory audit log with a ring buffer of max 200 entries.

    Security
    --------
    - ``emit()`` scrubs all string fields before storing.
    - JWT-like values ("eyJ...") in any field are replaced with [REDACTED_TOKEN].
    - IP addresses in string fields are replaced with [redacted_ip].
    - Artifact payloads must never be passed to ``emit()``; this is a caller
      responsibility enforced by code review.
    """

    def __init__(self, max_entries: int = _RING_MAX) -> None:
        self._max_entries = max_entries
        self._buf: collections.deque[WebRtcAuditEvent] = collections.deque(
            maxlen=max_entries
        )
        self._lock = threading.Lock()

    def emit(self, event: WebRtcAuditEvent) -> None:
        """Scrub and store the event in the ring buffer."""
        safe = WebRtcAuditEvent(
            event_type=_scrub(str(event.event_type)),
            session_id=_scrub(str(event.session_id)),
            peer_id_hash=_scrub(str(event.peer_id_hash)),
            error_category=_scrub(str(event.error_category)),
            timestamp=float(event.timestamp),
        )
        with self._lock:
            self._buf.append(safe)

    def recent(self, n: int = 20) -> list[WebRtcAuditEvent]:
        """Return the n most recent events (oldest first)."""
        with self._lock:
            entries = list(self._buf)
        if n <= 0:
            return []
        return entries[-n:]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)
