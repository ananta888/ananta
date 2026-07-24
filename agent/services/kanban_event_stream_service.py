"""Bounded replay and reconnect services for existing Hub Kanban events.

Mutation orchestration remains in ``KanbanProjectionService``.  This module is
only a projection sink and read model behind the existing ``KanbanEventPort``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from urllib.parse import quote

from ananta_contracts.kanban_events import (
    KanbanAuthRenewalContract,
    KanbanEvent,
    KanbanEventBatch,
    KanbanEventGapReason,
)
from agent.repositories.kanban_projection import (
    KanbanScope,
    SqlKanbanProjectionStore,
)


def _event_dedupe_key(
    board_id: str,
    task_id: str,
    revision: int,
    event_type: str,
) -> str:
    canonical = json.dumps(
        [board_id, task_id, revision, event_type],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class KanbanEventCandidate:
    board_id: str
    task_id: str
    revision: int
    event_type: str
    occurred_at: datetime
    payload: dict[str, str | int | float | bool | None]

    @property
    def dedupe_key(self) -> str:
        return _event_dedupe_key(
            self.board_id,
            self.task_id,
            self.revision,
            self.event_type,
        )


@dataclass(frozen=True, slots=True)
class KanbanEventPublishResult:
    event: KanbanEvent
    deduped: bool


@dataclass(frozen=True, slots=True)
class KanbanEventJournalRead:
    events: tuple[KanbanEvent, ...]
    latest_sequence: int
    has_more: bool
    deduped_events_total: int
    overflow_events_total: int
    gap_reason: KanbanEventGapReason | None = None


class KanbanEventJournalPort(Protocol):
    def append(
        self,
        candidate: KanbanEventCandidate,
    ) -> KanbanEventPublishResult: ...

    def read(
        self,
        *,
        board_id: str,
        after_sequence: int,
        limit: int,
    ) -> KanbanEventJournalRead: ...


@dataclass(slots=True)
class _BoardJournal:
    events: deque[KanbanEvent] = field(default_factory=deque)
    dedupe: OrderedDict[str, KanbanEvent] = field(default_factory=OrderedDict)
    next_sequence: int = 1
    deduped_events_total: int = 0
    overflow_events_total: int = 0


class InMemoryKanbanEventJournal:
    """Thread-safe bounded adapter; replaceable through ``KanbanEventJournalPort``."""

    def __init__(
        self,
        *,
        max_events_per_board: int = 512,
        dedupe_capacity_per_board: int | None = None,
    ) -> None:
        if max_events_per_board < 1:
            raise ValueError("kanban_event_capacity_invalid")
        self._max_events = max_events_per_board
        self._dedupe_capacity = (
            dedupe_capacity_per_board
            if dedupe_capacity_per_board is not None
            else max_events_per_board * 4
        )
        if self._dedupe_capacity < self._max_events:
            raise ValueError("kanban_event_dedupe_capacity_invalid")
        self._boards: dict[str, _BoardJournal] = {}
        self._lock = RLock()

    def append(
        self,
        candidate: KanbanEventCandidate,
    ) -> KanbanEventPublishResult:
        with self._lock:
            journal = self._boards.setdefault(
                candidate.board_id,
                _BoardJournal(),
            )
            existing = journal.dedupe.get(candidate.dedupe_key)
            if existing is not None:
                journal.deduped_events_total += 1
                journal.dedupe.move_to_end(candidate.dedupe_key)
                return KanbanEventPublishResult(event=existing, deduped=True)

            sequence = journal.next_sequence
            journal.next_sequence += 1
            event = KanbanEvent(
                event_id=str(sequence),
                board_id=candidate.board_id,
                task_id=candidate.task_id,
                revision=candidate.revision,
                sequence=sequence,
                event_type=candidate.event_type,
                occurred_at=candidate.occurred_at,
                payload=candidate.payload,
            )
            if len(journal.events) >= self._max_events:
                journal.events.popleft()
                journal.overflow_events_total += 1
            journal.events.append(event)
            journal.dedupe[candidate.dedupe_key] = event
            while len(journal.dedupe) > self._dedupe_capacity:
                journal.dedupe.popitem(last=False)
            return KanbanEventPublishResult(event=event, deduped=False)

    def read(
        self,
        *,
        board_id: str,
        after_sequence: int,
        limit: int,
    ) -> KanbanEventJournalRead:
        if after_sequence < 0 or limit < 1:
            raise ValueError("kanban_event_cursor_invalid")
        with self._lock:
            journal = self._boards.get(board_id)
            if journal is None:
                gap = (
                    KanbanEventGapReason.CLIENT_SEQUENCE_AHEAD
                    if after_sequence > 0
                    else None
                )
                return KanbanEventJournalRead(
                    events=(),
                    latest_sequence=0,
                    has_more=False,
                    deduped_events_total=0,
                    overflow_events_total=0,
                    gap_reason=gap,
                )

            latest = journal.next_sequence - 1
            retained = tuple(journal.events)
            oldest = retained[0].sequence if retained else latest + 1
            if after_sequence > latest:
                return KanbanEventJournalRead(
                    events=(),
                    latest_sequence=latest,
                    has_more=False,
                    deduped_events_total=journal.deduped_events_total,
                    overflow_events_total=journal.overflow_events_total,
                    gap_reason=KanbanEventGapReason.CLIENT_SEQUENCE_AHEAD,
                )
            if retained and after_sequence < oldest - 1:
                return KanbanEventJournalRead(
                    events=(),
                    latest_sequence=latest,
                    has_more=False,
                    deduped_events_total=journal.deduped_events_total,
                    overflow_events_total=journal.overflow_events_total,
                    gap_reason=KanbanEventGapReason.BOUNDED_HISTORY_OVERFLOW,
                )

            available = tuple(
                event for event in retained if event.sequence > after_sequence
            )
            expected = after_sequence + 1
            for event in available:
                if event.sequence != expected:
                    return KanbanEventJournalRead(
                        events=(),
                        latest_sequence=latest,
                        has_more=False,
                        deduped_events_total=journal.deduped_events_total,
                        overflow_events_total=journal.overflow_events_total,
                        gap_reason=KanbanEventGapReason.SEQUENCE_GAP,
                    )
                expected += 1
            selected = available[:limit]
            return KanbanEventJournalRead(
                events=selected,
                latest_sequence=latest,
                has_more=len(available) > len(selected),
                deduped_events_total=journal.deduped_events_total,
                overflow_events_total=journal.overflow_events_total,
            )

    def mirror(self, event: KanbanEvent) -> KanbanEventPublishResult:
        """Idempotently cache an already committed durable event."""

        key = _event_dedupe_key(
            event.board_id,
            event.task_id,
            event.revision,
            event.event_type,
        )
        with self._lock:
            journal = self._boards.setdefault(event.board_id, _BoardJournal())
            existing = journal.dedupe.get(key)
            if existing is not None:
                journal.deduped_events_total += 1
                journal.dedupe.move_to_end(key)
                return KanbanEventPublishResult(event=existing, deduped=True)
            same_sequence = next(
                (
                    retained
                    for retained in journal.events
                    if retained.sequence == event.sequence
                ),
                None,
            )
            if same_sequence is not None and same_sequence != event:
                raise ValueError("kanban_event_sequence_conflict")
            retained = sorted(
                [*journal.events, event],
                key=lambda item: item.sequence,
            )
            overflow = max(0, len(retained) - self._max_events)
            if overflow:
                retained = retained[overflow:]
                journal.overflow_events_total += overflow
            journal.events = deque(retained)
            journal.next_sequence = max(
                journal.next_sequence,
                event.sequence + 1,
            )
            journal.dedupe[key] = event
            while len(journal.dedupe) > self._dedupe_capacity:
                journal.dedupe.popitem(last=False)
            return KanbanEventPublishResult(event=event, deduped=False)


class SqlKanbanEventJournal:
    """Read-only durable journal; writes remain owned by projection commands."""

    def __init__(
        self,
        store_factory: Callable[[], SqlKanbanProjectionStore] | None = None,
    ) -> None:
        self._store_factory = store_factory or SqlKanbanProjectionStore

    def append(
        self,
        candidate: KanbanEventCandidate,
    ) -> KanbanEventPublishResult:
        del candidate
        raise RuntimeError("durable Kanban events must be written with the task")

    @staticmethod
    def _scope(board_id: str) -> KanbanScope:
        if board_id == "hub":
            return KanbanScope("hub")
        kind, separator, scope_id = board_id.partition(":")
        if separator and kind in {"goal", "team"} and scope_id:
            return KanbanScope(kind, scope_id)
        raise ValueError("kanban_board_not_found")

    def read(
        self,
        *,
        board_id: str,
        after_sequence: int,
        limit: int,
    ) -> KanbanEventJournalRead:
        result = self._store_factory().read_events(
            self._scope(board_id),
            after_sequence=after_sequence,
            limit=limit,
        )
        return KanbanEventJournalRead(
            events=result.events,
            latest_sequence=result.latest_sequence,
            has_more=result.has_more,
            deduped_events_total=0,
            overflow_events_total=0,
            gap_reason=result.gap_reason,
        )


class KanbanSnapshotReferencePort(Protocol):
    def snapshot_url(self, board_id: str) -> str: ...


class RestKanbanSnapshotReference:
    def snapshot_url(self, board_id: str) -> str:
        encoded = quote(board_id, safe="")
        return f"/api/v1/kanban/boards/{encoded}/snapshot"


def _task_revision(task: Any, details: Mapping[str, Any]) -> int:
    for value in (
        details.get("revision"),
        getattr(task, "kanban_revision", None),
        getattr(task, "revision", None),
        getattr(task, "version", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    for attribute in ("metadata", "task_metadata", "context"):
        value = getattr(task, attribute, None)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                continue
        if not isinstance(value, Mapping):
            continue
        for key in ("kanban_revision", "revision"):
            revision = value.get(key)
            if (
                isinstance(revision, int)
                and not isinstance(revision, bool)
                and revision >= 0
            ):
                return revision
    return 0


def _occurred_at(
    task: Any,
    clock: Callable[[], datetime],
) -> datetime:
    value = getattr(task, "updated_at", None)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return clock()


_OMITTED_PAYLOAD_FRAGMENTS = (
    "body",
    "description",
    "idempotency",
    "outcome",
    "path",
    "reason",
    "secret",
    "shell",
    "title",
    "token",
    "url",
)


def _minimal_payload(
    details: Mapping[str, Any],
) -> dict[str, str | int | float | bool | None]:
    payload: dict[str, str | int | float | bool | None] = {}
    for key in sorted(str(item) for item in details):
        normalized = key.strip().lower()
        if normalized in {"board_id", "revision"} or any(
            fragment in normalized for fragment in _OMITTED_PAYLOAD_FRAGMENTS
        ):
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", normalized):
            continue
        value = details.get(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            count_key = (
                "dependency_count"
                if normalized == "dependencies"
                else f"{normalized[:57]}_count"
            )
            payload[count_key] = len(value)
        elif isinstance(value, bool) or value is None:
            payload[normalized] = value
        elif isinstance(value, int):
            payload[normalized] = value
        elif isinstance(value, float) and math.isfinite(value):
            payload[normalized] = value
        elif isinstance(value, str):
            payload[normalized] = value[:256]
        if len(payload) == 8:
            break
    return payload


def build_kanban_event(
    *,
    action: str,
    task: Any,
    details: Mapping[str, Any],
    sequence: int,
    clock: Callable[[], datetime] | None = None,
) -> KanbanEvent:
    """Build the immutable event that is persisted with a task mutation."""

    if sequence < 1:
        raise ValueError("kanban_event_sequence_invalid")
    board_id = str(details.get("board_id") or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    if not board_id or not task_id:
        raise ValueError("kanban_event_identity_invalid")
    resolved_clock = clock or (lambda: datetime.now(tz=timezone.utc))
    return KanbanEvent(
        event_id=str(sequence),
        board_id=board_id,
        task_id=task_id,
        revision=_task_revision(task, details),
        sequence=sequence,
        event_type=str(action).strip().lower(),
        occurred_at=_occurred_at(task, resolved_clock),
        payload=_minimal_payload(details),
    )


class KanbanEventPublisher:
    def __init__(
        self,
        journal: KanbanEventJournalPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._journal = journal
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))

    def publish(
        self,
        *,
        action: str,
        task: Any,
        actor_id: str,
        details: dict[str, Any],
    ) -> KanbanEventPublishResult:
        del actor_id
        board_id = str(details.get("board_id") or "").strip()
        task_id = str(getattr(task, "id", "") or "").strip()
        if not board_id or not task_id:
            raise ValueError("kanban_event_identity_invalid")
        candidate = KanbanEventCandidate(
            board_id=board_id,
            task_id=task_id,
            revision=_task_revision(task, details),
            event_type=str(action).strip().lower(),
            occurred_at=_occurred_at(task, self._clock),
            payload=_minimal_payload(details),
        )
        return self._journal.append(candidate)


class KanbanEventReconnectService:
    def __init__(
        self,
        journal: KanbanEventJournalPort,
        *,
        snapshots: KanbanSnapshotReferencePort | None = None,
        auth_renewal: KanbanAuthRenewalContract | None = None,
    ) -> None:
        self._journal = journal
        self._snapshots = snapshots or RestKanbanSnapshotReference()
        self._auth_renewal = auth_renewal or KanbanAuthRenewalContract()

    def reconnect(
        self,
        *,
        board_id: str,
        after_sequence: int,
        limit: int = 100,
    ) -> KanbanEventBatch:
        read = self._journal.read(
            board_id=board_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        gap = read.gap_reason is not None
        next_sequence = (
            read.events[-1].sequence if read.events else after_sequence
        )
        overflow_reason = (
            read.gap_reason
            if read.gap_reason
            == KanbanEventGapReason.BOUNDED_HISTORY_OVERFLOW
            else None
        )
        return KanbanEventBatch(
            board_id=board_id,
            requested_after_sequence=after_sequence,
            next_after_sequence=next_sequence,
            latest_sequence=read.latest_sequence,
            events=read.events,
            has_more=read.has_more,
            deduped_events_total=read.deduped_events_total,
            overflow_events_total=read.overflow_events_total,
            overflow_reason=overflow_reason,
            gap_detected=gap,
            gap_reason=read.gap_reason,
            snapshot_required=gap,
            snapshot_url=(
                self._snapshots.snapshot_url(board_id) if gap else None
            ),
            auth_renewal=self._auth_renewal,
        )


class KanbanEventStreamService:
    """Small composition facade used by the existing event adapter and API."""

    def __init__(
        self,
        publisher: KanbanEventPublisher,
        reconnect: KanbanEventReconnectService,
        *,
        mirror: InMemoryKanbanEventJournal | None = None,
    ) -> None:
        self._publisher = publisher
        self._reconnect = reconnect
        self._mirror = mirror

    def publish(self, **kwargs: Any) -> KanbanEventPublishResult:
        return self._publisher.publish(**kwargs)

    def reconnect(self, **kwargs: Any) -> KanbanEventBatch:
        return self._reconnect.reconnect(**kwargs)

    def mirror(self, event: KanbanEvent) -> KanbanEventPublishResult:
        if self._mirror is None:
            raise RuntimeError("kanban_event_mirror_unavailable")
        return self._mirror.mirror(event)


def build_kanban_event_stream_service(
    *,
    max_events_per_board: int = 512,
) -> KanbanEventStreamService:
    journal = InMemoryKanbanEventJournal(
        max_events_per_board=max_events_per_board
    )
    return KanbanEventStreamService(
        KanbanEventPublisher(journal),
        KanbanEventReconnectService(journal),
        mirror=journal,
    )


def _configured_capacity() -> int:
    try:
        value = int(os.getenv("ANANTA_KANBAN_EVENT_BUFFER_SIZE", "512"))
    except ValueError:
        return 512
    return min(10_000, max(16, value))


_mirror_journal = InMemoryKanbanEventJournal(
    max_events_per_board=_configured_capacity()
)
_event_stream_service = KanbanEventStreamService(
    KanbanEventPublisher(_mirror_journal),
    KanbanEventReconnectService(SqlKanbanEventJournal()),
    mirror=_mirror_journal,
)


def get_kanban_event_stream_service() -> KanbanEventStreamService:
    return _event_stream_service
