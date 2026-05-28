from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditEvent:
    timestamp: float
    source_channel: str
    sender_kind: str
    raw_text_hash: str
    parsed_action_id: str
    policy_verdict: str     # "allow" | "deny" | "require_confirmation"
    dispatch_status: str    # "ok" | "error" | "skipped"
    mode: str               # "interactive_safe" | "autonomous_e2e"
    auto_confirmed: bool
    reason: str
    extra: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    def __init__(self, *, enabled: bool = True, max_events: int = 500) -> None:
        self.enabled = enabled
        self._events: list[AuditEvent] = []
        self._max_events = max_events

    def record(
        self,
        *,
        source_channel: str,
        sender_kind: str,
        raw_text: str,
        parsed_action_id: str,
        policy_verdict: str,
        dispatch_status: str,
        mode: str,
        auto_confirmed: bool = False,
        reason: str = "",
        extra: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            timestamp=time.time(),
            source_channel=source_channel,
            sender_kind=sender_kind,
            raw_text_hash=hashlib.sha256(raw_text.encode()).hexdigest()[:12],
            parsed_action_id=parsed_action_id,
            policy_verdict=policy_verdict,
            dispatch_status=dispatch_status,
            mode=mode,
            auto_confirmed=auto_confirmed,
            reason=reason,
            extra=dict(extra or {}),
        )
        if self.enabled:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]
        return event

    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def last(self, n: int = 10) -> list[AuditEvent]:
        return self._events[-n:]

    def clear(self) -> None:
        self._events.clear()


_default_log = AuditLog()


def get_default_audit_log() -> AuditLog:
    return _default_log
