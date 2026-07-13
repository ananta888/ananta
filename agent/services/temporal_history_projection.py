"""Incremental, restart-safe Temporal history projection.

Raw Temporal payloads are never copied into the hub database.  A strict
whitelist mapper emits canonical Ananta events plus a reference back to the
tenant-protected Temporal history.  Unknown mappings, sequence gaps and mapping
version changes fail closed as ``stale`` or ``inconsistent``.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from sqlmodel import Session, select

from agent.db_models import TemporalHistoryProjectionDB, TemporalProjectedEventDB
from agent.services.workflow_runtime import CanonicalWorkflowEvent

TEMPORAL_HISTORY_MAPPING_VERSION = "ananta.temporal-history-map.v1"
TEMPORAL_HISTORY_PAGE_SCHEMA = "ananta.temporal-history-projection-page.v1"
_CONSISTENCY_STATES = frozenset({"current", "stale", "inconsistent"})


class TemporalHistoryProjectionError(RuntimeError):
    def __init__(self, reason_code: str, *, consistency_state: str = "inconsistent") -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code or "temporal_history_projection_failed")
        self.consistency_state = consistency_state if consistency_state in _CONSISTENCY_STATES else "inconsistent"


@dataclass(frozen=True)
class TemporalHistoryRecord:
    event_id: int
    event_type: str
    occurred_at: float
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TemporalHistoryPage:
    records: tuple[TemporalHistoryRecord, ...]
    next_page_token: str = ""


@dataclass(frozen=True)
class TemporalProjectionCursor:
    projection_id: str
    namespace: str
    tenant_id: str
    workflow_id: str
    run_id: str
    temporal_run_id: str
    correlation_id: str
    last_event_id: int = 0
    next_page_token: str = ""
    mapping_version: str = TEMPORAL_HISTORY_MAPPING_VERSION
    consistency_state: str = "stale"
    reason_code: str = "projection_not_synchronized"
    lag_events: int | None = None
    revision: int = 1
    raw_history_ref: str = ""
    activity_step_map: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if any(
            not str(value).strip()
            for value in (
                self.projection_id,
                self.namespace,
                self.tenant_id,
                self.workflow_id,
                self.run_id,
                self.temporal_run_id,
                self.correlation_id,
                self.mapping_version,
                self.raw_history_ref,
            )
        ):
            raise TemporalHistoryProjectionError("projection_binding_required")
        if self.last_event_id < 0 or self.revision < 1:
            raise TemporalHistoryProjectionError("projection_cursor_invalid")
        if self.consistency_state not in _CONSISTENCY_STATES:
            raise TemporalHistoryProjectionError("projection_consistency_state_invalid")


class TemporalHistorySourcePort(Protocol):
    async def fetch_page(
        self,
        *,
        workflow_id: str,
        temporal_run_id: str,
        next_page_token: str,
        page_size: int,
    ) -> TemporalHistoryPage: ...


class TemporalProjectionRepositoryPort(Protocol):
    def get_cursor(self, *, namespace: str, workflow_id: str) -> TemporalProjectionCursor | None: ...

    def bind(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor: ...

    def commit_page(
        self,
        *,
        cursor: TemporalProjectionCursor,
        expected_revision: int,
        events: Sequence[CanonicalWorkflowEvent],
        temporal_types: dict[int, str],
    ) -> TemporalProjectionCursor: ...

    def list_events(self, *, projection_id: str, after_event_id: int, limit: int) -> tuple[dict[str, Any], ...]: ...

    def reset(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor: ...


class InMemoryTemporalProjectionRepository:
    def __init__(self) -> None:
        self._cursors: dict[tuple[str, str], TemporalProjectionCursor] = {}
        self._events: dict[str, dict[int, dict[str, Any]]] = {}

    def get_cursor(self, *, namespace: str, workflow_id: str) -> TemporalProjectionCursor | None:
        return self._cursors.get((str(namespace), str(workflow_id)))

    def bind(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor:
        cursor.validate()
        key = (cursor.namespace, cursor.workflow_id)
        existing = self._cursors.get(key)
        if existing is not None:
            if (
                existing.tenant_id,
                existing.run_id,
                existing.temporal_run_id,
            ) != (cursor.tenant_id, cursor.run_id, cursor.temporal_run_id):
                raise TemporalHistoryProjectionError("projection_binding_conflict")
            return existing
        self._cursors[key] = cursor
        return cursor

    def commit_page(
        self,
        *,
        cursor: TemporalProjectionCursor,
        expected_revision: int,
        events: Sequence[CanonicalWorkflowEvent],
        temporal_types: dict[int, str],
    ) -> TemporalProjectionCursor:
        key = (cursor.namespace, cursor.workflow_id)
        current = self._cursors.get(key)
        if current is None or current.revision != int(expected_revision):
            raise TemporalHistoryProjectionError("projection_revision_conflict", consistency_state="stale")
        storage = self._events.setdefault(cursor.projection_id, {})
        for event in events:
            existing = storage.get(event.sequence)
            payload = event.to_dict()
            if existing is not None and existing != payload:
                raise TemporalHistoryProjectionError("projection_event_conflict")
            storage[event.sequence] = payload
        self._cursors[key] = cursor
        return cursor

    def list_events(self, *, projection_id: str, after_event_id: int, limit: int) -> tuple[dict[str, Any], ...]:
        values = self._events.get(str(projection_id), {})
        return tuple(
            dict(values[event_id])
            for event_id in sorted(values)
            if event_id > int(after_event_id)
        )[: max(1, min(int(limit), 10_000))]

    def reset(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor:
        current = self._cursors.get((cursor.namespace, cursor.workflow_id))
        if current is None or current.tenant_id != cursor.tenant_id:
            raise TemporalHistoryProjectionError("projection_binding_conflict")
        reset_cursor = replace(
            current,
            last_event_id=0,
            next_page_token="",
            mapping_version=TEMPORAL_HISTORY_MAPPING_VERSION,
            consistency_state="stale",
            reason_code="projection_rebuild_started",
            lag_events=None,
            revision=current.revision + 1,
            activity_step_map={},
        )
        self._events[cursor.projection_id] = {}
        self._cursors[(cursor.namespace, cursor.workflow_id)] = reset_cursor
        return reset_cursor


class SQLTemporalProjectionRepository:
    def __init__(self, engine=None) -> None:
        self._engine_override = engine

    @property
    def _engine(self):
        if self._engine_override is not None:
            return self._engine_override
        from agent.database import engine

        return engine

    def get_cursor(self, *, namespace: str, workflow_id: str) -> TemporalProjectionCursor | None:
        projection_id = _projection_id(namespace, workflow_id)
        with Session(self._engine) as session:
            row = session.get(TemporalHistoryProjectionDB, projection_id)
            return _cursor_from_row(row) if row is not None else None

    def bind(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor:
        cursor.validate()
        with Session(self._engine) as session:
            existing = session.get(TemporalHistoryProjectionDB, cursor.projection_id)
            if existing is not None:
                current = _cursor_from_row(existing)
                if (
                    current.tenant_id,
                    current.run_id,
                    current.temporal_run_id,
                ) != (cursor.tenant_id, cursor.run_id, cursor.temporal_run_id):
                    raise TemporalHistoryProjectionError("projection_binding_conflict")
                return current
            session.add(_row_from_cursor(cursor))
            session.commit()
        return cursor

    def commit_page(
        self,
        *,
        cursor: TemporalProjectionCursor,
        expected_revision: int,
        events: Sequence[CanonicalWorkflowEvent],
        temporal_types: dict[int, str],
    ) -> TemporalProjectionCursor:
        cursor.validate()
        with Session(self._engine) as session:
            statement = (
                select(TemporalHistoryProjectionDB)
                .where(TemporalHistoryProjectionDB.id == cursor.projection_id)
                .with_for_update()
            )
            row = session.exec(statement).one_or_none()
            if row is None or int(row.revision) != int(expected_revision):
                raise TemporalHistoryProjectionError("projection_revision_conflict", consistency_state="stale")
            for event in events:
                existing = session.get(TemporalProjectedEventDB, event.event_id)
                payload = event.to_dict()
                if existing is not None:
                    if dict(existing.canonical_event or {}) != payload:
                        raise TemporalHistoryProjectionError("projection_event_conflict")
                    continue
                session.add(
                    TemporalProjectedEventDB(
                        id=event.event_id,
                        projection_id=cursor.projection_id,
                        tenant_id=cursor.tenant_id,
                        workflow_id=cursor.workflow_id,
                        run_id=cursor.run_id,
                        temporal_run_id=cursor.temporal_run_id,
                        temporal_event_id=event.sequence,
                        temporal_event_type=str(temporal_types.get(event.sequence) or "unknown"),
                        canonical_event=payload,
                        occurred_at=event.occurred_at,
                    )
                )
            _apply_cursor_to_row(row, cursor)
            session.add(row)
            session.commit()
        return cursor

    def list_events(self, *, projection_id: str, after_event_id: int, limit: int) -> tuple[dict[str, Any], ...]:
        with Session(self._engine) as session:
            statement = (
                select(TemporalProjectedEventDB)
                .where(TemporalProjectedEventDB.projection_id == str(projection_id))
                .where(TemporalProjectedEventDB.temporal_event_id > int(after_event_id))
                .order_by(TemporalProjectedEventDB.temporal_event_id.asc())
                .limit(max(1, min(int(limit), 10_000)))
            )
            return tuple(dict(row.canonical_event or {}) for row in session.exec(statement).all())

    def reset(self, cursor: TemporalProjectionCursor) -> TemporalProjectionCursor:
        with Session(self._engine) as session:
            statement = (
                select(TemporalHistoryProjectionDB)
                .where(TemporalHistoryProjectionDB.id == cursor.projection_id)
                .with_for_update()
            )
            row = session.exec(statement).one_or_none()
            if row is None or row.tenant_id != cursor.tenant_id:
                raise TemporalHistoryProjectionError("projection_binding_conflict")
            events = session.exec(
                select(TemporalProjectedEventDB).where(
                    TemporalProjectedEventDB.projection_id == cursor.projection_id
                )
            ).all()
            for event in events:
                session.delete(event)
            row.last_event_id = 0
            row.next_page_token = ""
            row.mapping_version = TEMPORAL_HISTORY_MAPPING_VERSION
            row.consistency_state = "stale"
            row.reason_code = "projection_rebuild_started"
            row.lag_events = None
            row.revision += 1
            row.activity_step_map = {}
            row.updated_at = time.time()
            session.add(row)
            session.commit()
            session.refresh(row)
            return _cursor_from_row(row)


class TemporalSDKHistorySource:
    """Lazy SDK source; importing the hub without Temporal remains supported."""

    def __init__(
        self,
        *,
        address: str,
        namespace: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._address = str(address)
        self._namespace = str(namespace)
        self._client_factory = client_factory

    async def fetch_page(
        self,
        *,
        workflow_id: str,
        temporal_run_id: str,
        next_page_token: str,
        page_size: int,
    ) -> TemporalHistoryPage:
        if self._client_factory is None:
            from temporalio.client import Client

            client = await Client.connect(self._address, namespace=self._namespace)
        else:
            candidate = self._client_factory()
            client = await candidate if hasattr(candidate, "__await__") else candidate
        handle = client.get_workflow_handle(str(workflow_id), run_id=str(temporal_run_id))
        iterator = handle.fetch_history_events(
            page_size=max(1, min(int(page_size), 10_000)),
            next_page_token=_decode_page_token(next_page_token),
        )
        await iterator.fetch_next_page()
        records = tuple(_record_from_temporal_event(event) for event in iterator.current_page or ())
        return TemporalHistoryPage(
            records=records,
            next_page_token=_encode_page_token(iterator.next_page_token),
        )


class TemporalHistoryEventMapper:
    def map(
        self,
        record: TemporalHistoryRecord,
        *,
        cursor: TemporalProjectionCursor,
        activity_step_map: dict[str, str],
    ) -> CanonicalWorkflowEvent:
        if record.event_id < 1 or not record.event_type.startswith("EVENT_TYPE_"):
            raise TemporalHistoryProjectionError("unknown_temporal_event_version")
        event_type = _canonical_event_type(record.event_type)
        step_id = _step_id_for(record, activity_step_map)
        event_id = _canonical_event_id(cursor, record.event_id)
        causation_id = (
            _canonical_event_id(cursor, record.event_id - 1)
            if record.event_id > 1
            else f"temporal:{cursor.namespace}:{cursor.workflow_id}:{cursor.temporal_run_id}:root"
        )
        safe_attributes = {
            key: value
            for key, value in record.attributes.items()
            if key
            in {
                "activity_id",
                "activity_type",
                "attempt",
                "marker_name",
                "scheduled_event_id",
                "signal_name",
                "timer_id",
                "workflow_type",
            }
        }
        return CanonicalWorkflowEvent.build(
            tenant_id=cursor.tenant_id,
            workflow_id=cursor.workflow_id,
            run_id=cursor.run_id,
            event_type=event_type,
            correlation_id=cursor.correlation_id,
            causation_id=causation_id,
            dedupe_key=event_id,
            step_id=step_id,
            attempt=int(safe_attributes.get("attempt") or 0),
            actor="temporal-runtime",
            occurred_at=record.occurred_at,
            event_id=event_id,
            payload={
                "mapping_version": cursor.mapping_version,
                "temporal_event_id": record.event_id,
                "temporal_event_type": record.event_type,
                "raw_history_ref": cursor.raw_history_ref,
                **safe_attributes,
            },
        ).with_sequence(record.event_id)


class TemporalHistoryProjectionService:
    def __init__(
        self,
        *,
        namespace: str,
        source: TemporalHistorySourcePort,
        repository: TemporalProjectionRepositoryPort,
        mapper: TemporalHistoryEventMapper | None = None,
    ) -> None:
        self._namespace = str(namespace)
        self._source = source
        self._repository = repository
        self._mapper = mapper or TemporalHistoryEventMapper()

    def bind_run(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        temporal_run_id: str,
        correlation_id: str,
    ) -> TemporalProjectionCursor:
        cursor = TemporalProjectionCursor(
            projection_id=_projection_id(self._namespace, workflow_id),
            namespace=self._namespace,
            tenant_id=str(tenant_id),
            workflow_id=str(workflow_id),
            run_id=str(run_id),
            temporal_run_id=str(temporal_run_id),
            correlation_id=str(correlation_id),
            raw_history_ref=(
                f"temporal://{self._namespace}/{workflow_id}/runs/{temporal_run_id}/history"
            ),
        )
        return self._repository.bind(cursor)

    async def synchronize(
        self,
        workflow_id: str,
        *,
        expected_tenant_id: str = "",
        page_size: int = 500,
        max_pages: int = 20,
    ) -> dict[str, Any]:
        cursor = self._repository.get_cursor(namespace=self._namespace, workflow_id=str(workflow_id))
        if cursor is None:
            return _unbound_page(workflow_id, "projection_binding_missing")
        if expected_tenant_id and cursor.tenant_id != str(expected_tenant_id):
            return _page_payload(cursor, (), reason_code="projection_tenant_mismatch", consistency="inconsistent")
        if cursor.mapping_version != TEMPORAL_HISTORY_MAPPING_VERSION:
            return _page_payload(cursor, (), reason_code="projection_mapping_upgrade_required", consistency="stale")

        seen_tokens: set[str] = set()
        for _ in range(max(1, min(int(max_pages), 1_000))):
            token = cursor.next_page_token
            if token and token in seen_tokens:
                cursor = self._commit_state(cursor, "inconsistent", "projection_page_token_loop", lag_events=None)
                break
            seen_tokens.add(token)
            try:
                page = await self._source.fetch_page(
                    workflow_id=cursor.workflow_id,
                    temporal_run_id=cursor.temporal_run_id,
                    next_page_token=token,
                    page_size=page_size,
                )
                cursor = self._apply_page(cursor, page)
            except TemporalHistoryProjectionError as exc:
                cursor = self._commit_state(cursor, exc.consistency_state, exc.reason_code, lag_events=None)
                break
            except Exception as exc:
                cursor = self._commit_state(
                    cursor,
                    "stale",
                    f"temporal_history_unavailable:{type(exc).__name__}",
                    lag_events=None,
                )
                break
            if not cursor.next_page_token:
                cursor = self._commit_state(cursor, "current", "", lag_events=0)
                break
        else:
            cursor = self._commit_state(
                cursor,
                "stale",
                "projection_page_budget_exhausted",
                lag_events=None,
            )
        events = self._repository.list_events(
            projection_id=cursor.projection_id,
            after_event_id=0,
            limit=10_000,
        )
        return _page_payload(cursor, events)

    async def rebuild(
        self,
        workflow_id: str,
        *,
        expected_tenant_id: str,
        page_size: int = 500,
        max_pages: int = 100,
    ) -> dict[str, Any]:
        cursor = self._repository.get_cursor(namespace=self._namespace, workflow_id=str(workflow_id))
        if cursor is None:
            return _unbound_page(workflow_id, "projection_binding_missing")
        if not expected_tenant_id or cursor.tenant_id != str(expected_tenant_id):
            return _page_payload(cursor, (), reason_code="projection_tenant_mismatch", consistency="inconsistent")
        self._repository.reset(cursor)
        return await self.synchronize(
            workflow_id,
            expected_tenant_id=expected_tenant_id,
            page_size=page_size,
            max_pages=max_pages,
        )

    def _apply_page(
        self,
        cursor: TemporalProjectionCursor,
        page: TemporalHistoryPage,
    ) -> TemporalProjectionCursor:
        records = sorted(page.records, key=lambda item: item.event_id)
        activity_map = dict(cursor.activity_step_map)
        mapped: list[CanonicalWorkflowEvent] = []
        temporal_types: dict[int, str] = {}
        last_event_id = cursor.last_event_id
        for record in records:
            if record.event_id <= last_event_id:
                continue
            if record.event_id != last_event_id + 1:
                raise TemporalHistoryProjectionError("temporal_history_gap")
            _capture_activity_step(record, activity_map)
            mapped.append(self._mapper.map(record, cursor=cursor, activity_step_map=activity_map))
            temporal_types[record.event_id] = record.event_type
            last_event_id = record.event_id
        updated = replace(
            cursor,
            last_event_id=last_event_id,
            next_page_token=page.next_page_token,
            consistency_state="stale" if page.next_page_token else cursor.consistency_state,
            reason_code="projection_in_progress" if page.next_page_token else cursor.reason_code,
            lag_events=None if page.next_page_token else cursor.lag_events,
            activity_step_map=activity_map,
            revision=cursor.revision + 1,
        )
        return self._repository.commit_page(
            cursor=updated,
            expected_revision=cursor.revision,
            events=mapped,
            temporal_types=temporal_types,
        )

    def _commit_state(
        self,
        cursor: TemporalProjectionCursor,
        consistency_state: str,
        reason_code: str,
        *,
        lag_events: int | None,
    ) -> TemporalProjectionCursor:
        updated = replace(
            cursor,
            consistency_state=consistency_state,
            reason_code=reason_code,
            lag_events=lag_events,
            revision=cursor.revision + 1,
        )
        return self._repository.commit_page(
            cursor=updated,
            expected_revision=cursor.revision,
            events=(),
            temporal_types={},
        )


def _record_from_temporal_event(event: Any) -> TemporalHistoryRecord:
    try:
        from temporalio.api.enums.v1 import EventType

        event_type = str(EventType.Name(int(event.event_type)))
    except Exception as exc:
        raise TemporalHistoryProjectionError("unknown_temporal_event_version") from exc
    occurred_at = float(event.event_time.seconds) + float(event.event_time.nanos) / 1_000_000_000
    attribute_name = str(event.WhichOneof("attributes") or "")
    attributes = getattr(event, attribute_name, None)
    safe: dict[str, Any] = {}
    if attributes is not None:
        for name in ("activity_id", "marker_name", "signal_name", "timer_id"):
            value = getattr(attributes, name, None)
            if value:
                safe[name] = str(value)[:512]
        for name in ("scheduled_event_id", "attempt"):
            value = getattr(attributes, name, None)
            if value:
                safe[name] = int(value)
        for name in ("activity_type", "workflow_type"):
            value = getattr(attributes, name, None)
            nested_name = getattr(value, "name", None)
            if nested_name:
                safe[name] = str(nested_name)[:512]
    return TemporalHistoryRecord(
        event_id=int(event.event_id),
        event_type=event_type,
        occurred_at=occurred_at,
        attributes=safe,
    )


def _canonical_event_type(temporal_type: str) -> str:
    exact = {
        "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED": "workflow.run.started",
        "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED": "workflow.run.completed",
        "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED": "workflow.run.failed",
        "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT": "workflow.run.failed",
        "EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED": "workflow.run.failed",
        "EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED": "workflow.run.cancelled",
        "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED": "workflow.command.received",
        "EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED": "workflow.command.accepted",
        "EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED": "workflow.command.completed",
        "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED": "workflow.step.scheduled",
        "EVENT_TYPE_ACTIVITY_TASK_STARTED": "workflow.step.started",
        "EVENT_TYPE_ACTIVITY_TASK_COMPLETED": "workflow.step.completed",
        "EVENT_TYPE_ACTIVITY_TASK_FAILED": "workflow.step.failed",
        "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT": "workflow.step.failed",
        "EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED": "workflow.step.cancel_requested",
        "EVENT_TYPE_ACTIVITY_TASK_CANCELED": "workflow.step.cancelled",
        "EVENT_TYPE_MARKER_RECORDED": "workflow.runtime.marker_recorded",
    }
    if temporal_type in exact:
        return exact[temporal_type]
    if temporal_type.startswith("EVENT_TYPE_WORKFLOW_TASK_"):
        return "workflow.runtime.workflow_task"
    if temporal_type.startswith("EVENT_TYPE_TIMER_"):
        return "workflow.runtime.timer"
    if temporal_type.startswith("EVENT_TYPE_WORKFLOW_EXECUTION_"):
        return "workflow.runtime.execution_event"
    if temporal_type.startswith("EVENT_TYPE_ACTIVITY_"):
        return "workflow.runtime.activity_event"
    if temporal_type.startswith("EVENT_TYPE_") and temporal_type != "EVENT_TYPE_UNSPECIFIED":
        return "workflow.runtime.history_event"
    raise TemporalHistoryProjectionError("unknown_temporal_event_version")


def _capture_activity_step(record: TemporalHistoryRecord, activity_map: dict[str, str]) -> None:
    if record.event_type != "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
        return
    activity_id = str(record.attributes.get("activity_id") or "")
    if activity_id.startswith("ananta:"):
        parts = activity_id.split(":", 2)
        if len(parts) == 3 and parts[1]:
            activity_map[str(record.event_id)] = parts[1]


def _step_id_for(record: TemporalHistoryRecord, activity_map: dict[str, str]) -> str:
    activity_id = str(record.attributes.get("activity_id") or "")
    if activity_id.startswith("ananta:"):
        parts = activity_id.split(":", 2)
        return parts[1] if len(parts) == 3 else ""
    scheduled_event_id = str(record.attributes.get("scheduled_event_id") or "")
    return str(activity_map.get(scheduled_event_id) or "")


def _projection_id(namespace: str, workflow_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{namespace}|{workflow_id}".encode("utf-8")).hexdigest()
    return f"thp-{digest[:40]}"


def _canonical_event_id(cursor: TemporalProjectionCursor, event_id: int) -> str:
    return (
        f"temporal:{cursor.namespace}:{cursor.workflow_id}:"
        f"{cursor.temporal_run_id}:{int(event_id)}:{cursor.mapping_version}"
    )


def _encode_page_token(value: bytes | None) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii") if value else ""


def _decode_page_token(value: str) -> bytes | None:
    if not value:
        return None
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise TemporalHistoryProjectionError("invalid_projection_page_token") from exc


def _row_from_cursor(cursor: TemporalProjectionCursor) -> TemporalHistoryProjectionDB:
    now = time.time()
    return TemporalHistoryProjectionDB(
        id=cursor.projection_id,
        namespace=cursor.namespace,
        workflow_id=cursor.workflow_id,
        tenant_id=cursor.tenant_id,
        run_id=cursor.run_id,
        temporal_run_id=cursor.temporal_run_id,
        correlation_id=cursor.correlation_id,
        last_event_id=cursor.last_event_id,
        next_page_token=cursor.next_page_token,
        mapping_version=cursor.mapping_version,
        consistency_state=cursor.consistency_state,
        reason_code=cursor.reason_code,
        lag_events=cursor.lag_events,
        revision=cursor.revision,
        raw_history_ref=cursor.raw_history_ref,
        activity_step_map=dict(cursor.activity_step_map),
        created_at=now,
        updated_at=now,
    )


def _cursor_from_row(row: TemporalHistoryProjectionDB) -> TemporalProjectionCursor:
    return TemporalProjectionCursor(
        projection_id=row.id,
        namespace=row.namespace,
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        temporal_run_id=row.temporal_run_id,
        correlation_id=row.correlation_id,
        last_event_id=row.last_event_id,
        next_page_token=row.next_page_token,
        mapping_version=row.mapping_version,
        consistency_state=row.consistency_state,
        reason_code=row.reason_code,
        lag_events=row.lag_events,
        revision=row.revision,
        raw_history_ref=row.raw_history_ref,
        activity_step_map=dict(row.activity_step_map or {}),
    )


def _apply_cursor_to_row(row: TemporalHistoryProjectionDB, cursor: TemporalProjectionCursor) -> None:
    row.last_event_id = cursor.last_event_id
    row.next_page_token = cursor.next_page_token
    row.mapping_version = cursor.mapping_version
    row.consistency_state = cursor.consistency_state
    row.reason_code = cursor.reason_code
    row.lag_events = cursor.lag_events
    row.revision = cursor.revision
    row.activity_step_map = dict(cursor.activity_step_map)
    row.updated_at = time.time()


def _page_payload(
    cursor: TemporalProjectionCursor,
    events: Sequence[dict[str, Any]],
    *,
    reason_code: str | None = None,
    consistency: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": TEMPORAL_HISTORY_PAGE_SCHEMA,
        "workflow_id": cursor.workflow_id,
        "run_id": cursor.run_id,
        "events": [dict(event) for event in events],
        "projection_cursor": cursor.last_event_id,
        "mapping_version": cursor.mapping_version,
        "lag": cursor.lag_events,
        "consistency_state": consistency or cursor.consistency_state,
        "reason_code": cursor.reason_code if reason_code is None else reason_code,
        "raw_history_ref": cursor.raw_history_ref,
    }


def _unbound_page(workflow_id: str, reason_code: str) -> dict[str, Any]:
    return {
        "schema": TEMPORAL_HISTORY_PAGE_SCHEMA,
        "workflow_id": str(workflow_id),
        "run_id": "",
        "events": [],
        "projection_cursor": 0,
        "mapping_version": TEMPORAL_HISTORY_MAPPING_VERSION,
        "lag": None,
        "consistency_state": "inconsistent",
        "reason_code": reason_code,
        "raw_history_ref": "",
    }


__all__ = [
    "InMemoryTemporalProjectionRepository",
    "SQLTemporalProjectionRepository",
    "TEMPORAL_HISTORY_MAPPING_VERSION",
    "TEMPORAL_HISTORY_PAGE_SCHEMA",
    "TemporalHistoryEventMapper",
    "TemporalHistoryPage",
    "TemporalHistoryProjectionError",
    "TemporalHistoryProjectionService",
    "TemporalHistoryRecord",
    "TemporalProjectionCursor",
    "TemporalSDKHistorySource",
]
