"""Typed, bounded stream projection over Hub-owned workflow histories.

The stream is a transport projection only. Canonical workflow events remain
the source of truth and clients resume with the returned opaque cursor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.services.workflow_runtime._serialization import redact_json

WORKFLOW_STREAM_REQUEST_SCHEMA = "ananta.workflow_stream_request.v1"
WORKFLOW_STREAM_FRAME_SCHEMA = "ananta.workflow_stream_frame.v1"
WORKFLOW_STREAM_BATCH_SCHEMA = "ananta.workflow_stream_batch.v1"

CANONICAL_STREAM_EVENT_TYPES = frozenset(
    {
        "workflow.token.delta",
        "workflow.tool.started",
        "workflow.tool.completed",
        "workflow.tool.failed",
        "workflow.node.started",
        "workflow.node.completed",
        "workflow.node.failed",
        "workflow.approval.requested",
        "workflow.approval.approved",
        "workflow.approval.rejected",
        "workflow.budget.updated",
        "workflow.status.updated",
        "workflow.run.started",
        "workflow.run.completed",
        "workflow.run.failed",
        "workflow.run.cancelled",
        "workflow.stream.heartbeat",
        "workflow.stream.backpressure",
    }
)

_LEGACY_TYPES = {
    "workflow_started": "workflow.run.started",
    "workflow_completed": "workflow.run.completed",
    "workflow_failed": "workflow.run.failed",
    "workflow_cancelled": "workflow.run.cancelled",
    "step_started": "workflow.node.started",
    "step_completed": "workflow.node.completed",
    "step_failed": "workflow.node.failed",
    "approval_requested": "workflow.approval.requested",
    "signal:approve": "workflow.approval.approved",
    "signal:reject": "workflow.approval.rejected",
    "budget_updated": "workflow.budget.updated",
    "token": "workflow.token.delta",
    "tool_started": "workflow.tool.started",
    "tool_completed": "workflow.tool.completed",
    "tool_failed": "workflow.tool.failed",
}


class WorkflowStreamError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class WorkflowStreamRequest:
    workflow_id: str
    after_cursor: str = ""
    max_events: int = 128
    wait_seconds: float = 0.0
    heartbeat_seconds: float = 15.0
    schema: str = WORKFLOW_STREAM_REQUEST_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowStreamRequest":
        allowed = {
            "schema",
            "workflow_id",
            "after_cursor",
            "max_events",
            "wait_seconds",
            "heartbeat_seconds",
        }
        if set(raw) - allowed:
            raise WorkflowStreamError("workflow_stream_unknown_field")
        request = cls(
            schema=str(raw.get("schema") or ""),
            workflow_id=str(raw.get("workflow_id") or "").strip(),
            after_cursor=str(raw.get("after_cursor") or "").strip(),
            max_events=_integer(raw.get("max_events"), default=128),
            wait_seconds=_number(raw.get("wait_seconds"), default=0.0),
            heartbeat_seconds=_number(raw.get("heartbeat_seconds"), default=15.0),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema != WORKFLOW_STREAM_REQUEST_SCHEMA:
            raise WorkflowStreamError("workflow_stream_schema_unsupported")
        if not self.workflow_id or len(self.workflow_id) > 160:
            raise WorkflowStreamError("workflow_stream_workflow_id_invalid")
        _decode_cursor(self.after_cursor)
        if not 1 <= self.max_events <= 256:
            raise WorkflowStreamError("workflow_stream_max_events_invalid")
        if not 0 <= self.wait_seconds <= 30:
            raise WorkflowStreamError("workflow_stream_wait_invalid")
        if not 1 <= self.heartbeat_seconds <= 30:
            raise WorkflowStreamError("workflow_stream_heartbeat_invalid")


@dataclass(frozen=True)
class WorkflowStreamFrame:
    event_type: str
    workflow_id: str
    cursor: str
    event_id: str
    occurred_at: float
    run_id: str = ""
    step_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    schema: str = WORKFLOW_STREAM_FRAME_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_type": self.event_type,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "cursor": self.cursor,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class WorkflowStreamBatch:
    frames: tuple[WorkflowStreamFrame, ...]
    next_cursor: str
    has_more: bool
    schema: str = WORKFLOW_STREAM_BATCH_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frames": [frame.to_dict() for frame in self.frames],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


class WorkflowHistoryPort(Protocol):
    def list_workflow_events(self, workflow_id: str) -> Sequence[Mapping[str, Any]]: ...


class WorkflowStreamService:
    """Projects a bounded page and never buffers an unbounded runtime stream."""

    def __init__(
        self,
        history: WorkflowHistoryPort,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        sleeper=time.sleep,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._history = history
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._poll_interval_seconds = max(
            0.01, min(float(poll_interval_seconds), 1.0)
        )

    def read(
        self,
        request: WorkflowStreamRequest,
        *,
        disconnected: Callable[[], bool] | None = None,
    ) -> WorkflowStreamBatch:
        request.validate()
        offset = _decode_cursor(request.after_cursor)
        events = self._wait_for_events(
            request,
            offset=offset,
            disconnected=disconnected,
        )
        if offset > len(events):
            raise WorkflowStreamError("workflow_stream_cursor_ahead")

        available = events[offset:]
        selected = available[: request.max_events]
        frames = tuple(
            self._frame(request.workflow_id, raw, position=offset + index + 1) for index, raw in enumerate(selected)
        )
        next_offset = offset + len(selected)
        has_more = len(available) > len(selected)
        if not frames:
            frames = (
                WorkflowStreamFrame(
                    event_type="workflow.stream.heartbeat",
                    workflow_id=request.workflow_id,
                    cursor=_encode_cursor(offset),
                    event_id=f"heartbeat:{offset}",
                    occurred_at=float(self._clock()),
                    payload={"retry_after_seconds": request.heartbeat_seconds},
                ),
            )
        elif has_more:
            frames += (
                WorkflowStreamFrame(
                    event_type="workflow.stream.backpressure",
                    workflow_id=request.workflow_id,
                    cursor=_encode_cursor(next_offset),
                    event_id=f"backpressure:{next_offset}",
                    occurred_at=float(self._clock()),
                    payload={"remaining_events": len(available) - len(selected)},
                ),
            )
        return WorkflowStreamBatch(
            frames=frames,
            next_cursor=_encode_cursor(next_offset),
            has_more=has_more,
        )

    def _wait_for_events(
        self,
        request: WorkflowStreamRequest,
        *,
        offset: int,
        disconnected: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        events = tuple(self._history.list_workflow_events(request.workflow_id))
        if offset > len(events):
            raise WorkflowStreamError("workflow_stream_cursor_ahead")
        if len(events) > offset or request.wait_seconds <= 0:
            return events
        wait_limit = min(request.wait_seconds, request.heartbeat_seconds)
        deadline = float(self._monotonic()) + wait_limit
        while float(self._monotonic()) < deadline:
            if disconnected is not None and bool(disconnected()):
                raise WorkflowStreamError("workflow_stream_disconnected")
            remaining = deadline - float(self._monotonic())
            self._sleeper(min(self._poll_interval_seconds, max(0.0, remaining)))
            events = tuple(self._history.list_workflow_events(request.workflow_id))
            if offset > len(events):
                raise WorkflowStreamError("workflow_stream_cursor_ahead")
            if len(events) > offset:
                return events
        return events

    def _frame(
        self,
        workflow_id: str,
        raw: Mapping[str, Any],
        *,
        position: int,
    ) -> WorkflowStreamFrame:
        if not isinstance(raw, Mapping):
            raise WorkflowStreamError("workflow_stream_event_invalid")
        raw_type = str(raw.get("event_type") or "").strip()
        event_type = _canonical_event_type(raw_type)
        raw_payload = raw.get("payload")
        if not isinstance(raw_payload, Mapping):
            raw_payload = raw.get("details") if isinstance(raw.get("details"), Mapping) else {}
        payload = dict(redact_json(dict(raw_payload)))
        if event_type == "workflow.status.updated" and raw_type:
            payload["source_event_type"] = raw_type[:128]
        rendered_size = len(str(payload).encode("utf-8"))
        if rendered_size > 64 * 1024:
            payload = {
                "reason_code": "workflow_stream_payload_redacted",
                "original_size_bytes": rendered_size,
            }
        step_id = str(raw.get("step_id") or payload.get("step_id") or "")[:160]
        return WorkflowStreamFrame(
            event_type=event_type,
            workflow_id=workflow_id,
            run_id=str(raw.get("run_id") or "")[:160],
            step_id=step_id,
            cursor=_encode_cursor(position),
            event_id=str(raw.get("event_id") or f"stream:{position}")[:256],
            occurred_at=float(raw.get("occurred_at") or raw.get("timestamp") or self._clock()),
            payload=payload,
        )


def _canonical_event_type(raw_type: str) -> str:
    if raw_type in CANONICAL_STREAM_EVENT_TYPES:
        return raw_type
    if raw_type.startswith(
        (
            "workflow.token.",
            "workflow.tool.",
            "workflow.node.",
            "workflow.approval.",
            "workflow.budget.",
            "workflow.status.",
            "workflow.run.",
            "workflow.step.",
        )
    ):
        return raw_type[:128]
    return _LEGACY_TYPES.get(raw_type, "workflow.status.updated")


def _encode_cursor(offset: int) -> str:
    return f"v1:{max(0, int(offset))}"


def _decode_cursor(cursor: str) -> int:
    if not cursor:
        return 0
    prefix, separator, raw_offset = cursor.partition(":")
    if separator != ":" or prefix != "v1" or not raw_offset.isdigit():
        raise WorkflowStreamError("workflow_stream_cursor_invalid")
    offset = int(raw_offset)
    if offset > 10_000_000:
        raise WorkflowStreamError("workflow_stream_cursor_invalid")
    return offset


def _integer(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise WorkflowStreamError("workflow_stream_number_invalid")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowStreamError("workflow_stream_number_invalid") from exc


def _number(value: Any, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise WorkflowStreamError("workflow_stream_number_invalid")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowStreamError("workflow_stream_number_invalid") from exc


__all__ = [
    "CANONICAL_STREAM_EVENT_TYPES",
    "WORKFLOW_STREAM_BATCH_SCHEMA",
    "WORKFLOW_STREAM_FRAME_SCHEMA",
    "WORKFLOW_STREAM_REQUEST_SCHEMA",
    "WorkflowHistoryPort",
    "WorkflowStreamBatch",
    "WorkflowStreamError",
    "WorkflowStreamFrame",
    "WorkflowStreamRequest",
    "WorkflowStreamService",
]
