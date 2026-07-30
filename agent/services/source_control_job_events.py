"""Tenant-bound, content-free source/index job event projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_EVENT_TYPES = frozenset(
    {
        "source_refresh",
        "source_scan",
        "source_admission",
        "index_queued",
        "index_started",
        "index_progress",
        "index_completed",
        "index_failed",
        "index_cancelled",
        "index_activated",
        "index_rolled_back",
        "index_reconciled",
    }
)


class SourceControlJobEventError(ValueError):
    pass


@dataclass(frozen=True)
class SourceControlJobEvent:
    event_id: str
    sequence: int
    tenant_id: str
    project_id: str
    resource_id: str
    job_id: str
    event_type: str
    status: str
    reason_code: str | None
    trace_id: str
    occurred_at: str

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "tenant_id",
            "project_id",
            "resource_id",
            "job_id",
            "status",
            "trace_id",
            "occurred_at",
        ):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise SourceControlJobEventError(f"{name}_invalid")
        if self.sequence < 1:
            raise SourceControlJobEventError("sequence_invalid")
        if self.event_type not in _EVENT_TYPES:
            raise SourceControlJobEventError("event_type_invalid")
        if self.reason_code is not None and not _OPAQUE_ID.fullmatch(
            self.reason_code
        ):
            raise SourceControlJobEventError("reason_code_invalid")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "resource_id": self.resource_id,
            "job_id": self.job_id,
            "event_type": self.event_type,
            "status": self.status,
            "reason_code": self.reason_code,
            "trace_id": self.trace_id,
            "occurred_at": self.occurred_at,
        }


class SourceControlJobEventPort(Protocol):
    """Persistent outbox reader.

    Implementations must order by a durable database sequence. A sequence
    derived from timestamps, generations, or page-local ordering is invalid.
    """

    def read_after(
        self,
        *,
        tenant_id: str,
        project_id: str,
        after_sequence: int,
        limit: int,
    ) -> Sequence[SourceControlJobEvent]: ...


class SourceControlJobEventService:
    def __init__(self, events: SourceControlJobEventPort) -> None:
        self._events = events

    def poll(
        self,
        *,
        tenant_id: str,
        project_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        if after_sequence < 0:
            raise SourceControlJobEventError("after_sequence_invalid")
        if limit < 1 or limit > 500:
            raise SourceControlJobEventError("limit_invalid")
        events = tuple(
            self._events.read_after(
                tenant_id=tenant_id,
                project_id=project_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        )
        previous = after_sequence
        for event in events:
            if (
                event.tenant_id != tenant_id
                or event.project_id != project_id
                or event.sequence <= previous
            ):
                raise SourceControlJobEventError("event_scope_or_sequence_invalid")
            previous = event.sequence
        return {
            "schema": "ananta.source-control.job-events.v1",
            "events": [event.to_public_dict() for event in events],
            "next_sequence": previous,
        }
