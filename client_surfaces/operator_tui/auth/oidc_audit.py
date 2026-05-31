"""Structured audit log for OIDC browser session events.

Emits redacted audit events for OIDC activity — never contains token values,
authorization codes, or cookie content.

Event types:
    login_start, callback_received, state_validated, token_exchange_success,
    token_exchange_failed, provider_rejected, logout, profile_cleanup

Security invariants:
- No token-like values in any event field or string representation.
- In-memory ring buffer only (max 200 events) — no disk persistence.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


# Event type constants
EVENT_LOGIN_START = "login_start"
EVENT_CALLBACK_RECEIVED = "callback_received"
EVENT_STATE_VALIDATED = "state_validated"
EVENT_TOKEN_EXCHANGE_SUCCESS = "token_exchange_success"
EVENT_TOKEN_EXCHANGE_FAILED = "token_exchange_failed"
EVENT_PROVIDER_REJECTED = "provider_rejected"
EVENT_LOGOUT = "logout"
EVENT_PROFILE_CLEANUP = "profile_cleanup"

# Mode constants
MODE_ANANTA_OWNED = "ananta_owned_callback"
MODE_REAL_BROWSER = "real_browser_session"

# Profile mode constants
PROFILE_EPHEMERAL = "ephemeral"
PROFILE_NAMED = "named"

_MAX_BUFFER = 200


@dataclass
class OidcAuditEvent:
    """A single redacted OIDC audit event.

    All fields contain only safe, non-secret metadata.
    Token values, auth codes, and cookies are NEVER stored here.
    """
    event_type: str  # one of the EVENT_* constants
    provider_id: str
    mode: str  # ananta_owned_callback | real_browser_session
    profile_mode: str  # ephemeral | named
    error_category: str  # empty string if success
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return (
            f"OidcAuditEvent("
            f"event_type={self.event_type!r}, "
            f"provider_id={self.provider_id!r}, "
            f"mode={self.mode!r}, "
            f"profile_mode={self.profile_mode!r}, "
            f"error_category={self.error_category!r}, "
            f"timestamp={self.timestamp:.3f})"
        )

    def __repr__(self) -> str:
        return self.__str__()


class OidcAuditLog:
    """Thread-safe in-memory ring buffer of OIDC audit events.

    Stores at most 200 events.  Oldest events are dropped when the buffer
    is full.  No persistence — survives only for the process lifetime.
    """

    def __init__(self, max_events: int = _MAX_BUFFER) -> None:
        self._max_events = max_events
        self._buffer: deque[OidcAuditEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def emit(self, event: OidcAuditEvent) -> None:
        """Append an event to the ring buffer.

        Args:
            event: The ``OidcAuditEvent`` to record.
        """
        with self._lock:
            self._buffer.append(event)

    def recent(self, n: int = 20) -> list[OidcAuditEvent]:
        """Return the most recent *n* events (newest last).

        Args:
            n: Maximum number of events to return.

        Returns:
            List of up to *n* most recent events, oldest first.
        """
        with self._lock:
            events = list(self._buffer)
        return events[-n:] if n < len(events) else events

    def clear(self) -> None:
        """Remove all events from the buffer."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
