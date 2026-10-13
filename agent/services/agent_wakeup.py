"""Hub-native wakeup requests with coalescing and idempotency (PAO, from paperclip).

Agents/teams are woken plan- or event-driven. A wakeup is a hub-owned request,
never a worker-owned loop: duplicates with the same idempotency key are ignored,
and repeated triggers for the same agent/source coalesce into one pending request
(``coalesced_count``), so a burst of events never starts a burst of runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace

_SOURCES = frozenset({"on_demand", "schedule", "assignment", "event", "manual", "recovery"})


@dataclass(frozen=True, slots=True)
class WakeupRequest:
    request_id: str
    agent_id: str
    source: str
    status: str
    trigger_detail: str | None
    reason: str | None
    idempotency_key: str | None
    coalesced_count: int
    requested_at: float
    claimed_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    def to_mapping(self) -> dict:
        return {"request_id": self.request_id, "agent_id": self.agent_id, "source": self.source,
                "status": self.status, "trigger_detail": self.trigger_detail, "reason": self.reason,
                "idempotency_key": self.idempotency_key, "coalesced_count": self.coalesced_count,
                "requested_at": self.requested_at, "claimed_at": self.claimed_at,
                "finished_at": self.finished_at, "error": self.error}


@dataclass
class WakeupQueue:
    """In-memory hub port; the production adapter persists the same fields."""

    clock: Callable[[], float] = lambda: 0.0
    _requests: dict[str, WakeupRequest] = field(default_factory=dict)
    _by_idempotency: dict[str, str] = field(default_factory=dict)

    def enqueue(self, agent_id: str, source: str, *, trigger_detail: str | None = None, reason: str | None = None,
                idempotency_key: str | None = None) -> WakeupRequest:
        if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 200:
            raise ValueError("wakeup_agent_id_invalid")
        if source not in _SOURCES:
            raise ValueError("wakeup_source_invalid")
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 200:
                raise ValueError("wakeup_idempotency_key_invalid")
            existing_id = self._by_idempotency.get(idempotency_key)
            if existing_id is not None:
                return self._requests[existing_id]
        pending = self._find_pending(agent_id, source)
        if pending is not None:
            coalesced = self._replace(pending, coalesced_count=pending.coalesced_count + 1)
            if idempotency_key is not None:
                self._by_idempotency[idempotency_key] = coalesced.request_id
            return coalesced
        now = self.clock()
        request = WakeupRequest(request_id=uuid.uuid4().hex, agent_id=agent_id, source=source, status="queued",
                                trigger_detail=trigger_detail, reason=reason, idempotency_key=idempotency_key,
                                coalesced_count=0, requested_at=now)
        self._requests[request.request_id] = request
        if idempotency_key is not None:
            self._by_idempotency[idempotency_key] = request.request_id
        return request

    def claim(self, *, agent_id: str | None = None) -> WakeupRequest | None:
        for request in self._requests.values():
            if request.status == "queued" and (agent_id is None or request.agent_id == agent_id):
                return self._replace(request, status="claimed", claimed_at=self.clock())
        return None

    def finish(self, request_id: str, *, error: str | None = None) -> WakeupRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise ValueError("wakeup_request_unknown")
        if request.status not in {"claimed", "queued"}:
            raise ValueError("wakeup_request_not_open")
        return self._replace(request, status="failed" if error else "finished", finished_at=self.clock(),
                             error=error)

    def pending(self, *, agent_id: str | None = None) -> list[WakeupRequest]:
        return [request for request in self._requests.values()
                if request.status in {"queued", "claimed"} and (agent_id is None or request.agent_id == agent_id)]

    def _find_pending(self, agent_id: str, source: str) -> WakeupRequest | None:
        for request in self._requests.values():
            if request.agent_id == agent_id and request.source == source and request.status == "queued":
                return request
        return None

    def _replace(self, request: WakeupRequest, **changes) -> WakeupRequest:
        updated = replace(request, **changes)
        self._requests[request.request_id] = updated
        return updated
