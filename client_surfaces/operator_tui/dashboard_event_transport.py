from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.kanban_events import KanbanEvent


class KanbanEventTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 0,
        retryable: bool = True,
    ) -> None:
        self.code = str(code or "kanban_event_transport_error")
        self.status_code = int(status_code)
        self.retryable = bool(retryable)
        super().__init__(self.code)


class KanbanEventContractError(KanbanEventTransportError):
    def __init__(self, code: str) -> None:
        super().__init__(code, status_code=502, retryable=False)


@dataclass(frozen=True)
class KanbanEventBatch:
    events: tuple[KanbanEvent, ...]
    gap_detected: bool
    gap_reason: str
    overflow_reason: str
    snapshot_required: bool
    snapshot_url: str
    next_after_sequence: int
    latest_sequence: int
    has_more: bool

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        after_sequence: int,
        max_events: int,
    ) -> "KanbanEventBatch":
        source: Any = raw.get("data", raw)
        if not isinstance(source, Mapping):
            raise KanbanEventContractError("kanban_event_batch_invalid")
        raw_events = source.get("events", [])
        if not isinstance(raw_events, list) or len(raw_events) > max_events:
            raise KanbanEventContractError("kanban_event_batch_size_invalid")
        try:
            events = tuple(KanbanEvent.model_validate(item) for item in raw_events)
        except Exception as exc:
            raise KanbanEventContractError("kanban_event_contract_invalid") from exc

        fallback_sequence = events[-1].sequence if events else after_sequence
        next_sequence = _strict_sequence(
            source.get("next_after_sequence", fallback_sequence),
            code="kanban_event_next_sequence_invalid",
        )
        latest_sequence = _strict_sequence(
            source.get("latest_sequence", max(next_sequence, fallback_sequence)),
            code="kanban_event_latest_sequence_invalid",
        )
        if latest_sequence < next_sequence:
            raise KanbanEventContractError("kanban_event_sequence_range_invalid")
        return cls(
            events=events,
            gap_detected=source.get("gap_detected") is True,
            gap_reason=str(source.get("gap_reason") or ""),
            overflow_reason=str(source.get("overflow_reason") or ""),
            snapshot_required=source.get("snapshot_required") is True,
            snapshot_url=str(source.get("snapshot_url") or ""),
            next_after_sequence=next_sequence,
            latest_sequence=latest_sequence,
            has_more=source.get("has_more") is True,
        )


class KanbanEventTransport(Protocol):
    async def fetch(
        self,
        *,
        board_id: str,
        after_sequence: int,
        token: str,
    ) -> KanbanEventBatch:
        ...

    def close(self) -> None:
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedKanbanEventTransport:
    """Bounded replay transport with JSON-batch and SSE-frame support."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float = 5.0,
        max_events: int = 200,
        max_response_bytes: int = 2_097_152,
        max_event_bytes: int = 65_536,
    ) -> None:
        base = str(endpoint or "").strip().rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("dashboard_hub_endpoint_invalid")
        self._endpoint = base
        self._timeout_seconds = max(0.25, min(30.0, float(timeout_seconds)))
        self._max_events = max(1, min(1000, int(max_events)))
        self._max_response_bytes = max(1024, int(max_response_bytes))
        self._max_event_bytes = max(256, int(max_event_bytes))
        self._closed = threading.Event()
        self._response_lock = threading.Lock()
        self._active_response: Any = None

    async def fetch(
        self,
        *,
        board_id: str,
        after_sequence: int,
        token: str,
    ) -> KanbanEventBatch:
        return await asyncio.to_thread(
            self._fetch_sync,
            board_id=board_id,
            after_sequence=after_sequence,
            token=token,
        )

    def close(self) -> None:
        self._closed.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def _fetch_sync(
        self,
        *,
        board_id: str,
        after_sequence: int,
        token: str,
    ) -> KanbanEventBatch:
        if self._closed.is_set():
            raise KanbanEventTransportError(
                "kanban_event_transport_closed",
                retryable=False,
            )
        sequence = _strict_sequence(
            after_sequence,
            code="kanban_event_cursor_invalid",
        )
        encoded_board = urllib.parse.quote(str(board_id or "").strip(), safe="")
        if not encoded_board:
            raise KanbanEventContractError("kanban_event_board_required")
        query = urllib.parse.urlencode(
            {"after_sequence": str(sequence), "limit": str(self._max_events)}
        )
        request = urllib.request.Request(
            f"{self._endpoint}/api/v1/kanban/boards/{encoded_board}/events?{query}",
            headers={
                "Accept": "text/event-stream, application/json",
                "Authorization": f"Bearer {str(token or '').strip()}",
                "Last-Event-ID": str(sequence),
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=self._timeout_seconds) as response:
                with self._response_lock:
                    self._active_response = response
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" in content_type:
                    raw = self._read_one_sse_delivery(response)
                    return parse_sse_payload(
                        raw,
                        after_sequence=sequence,
                        max_events=self._max_events,
                        max_event_bytes=self._max_event_bytes,
                    )
                raw = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raw_error = exc.read(min(self._max_response_bytes + 1, 65_537))
            code = _http_error_code(raw_error, exc.code)
            raise KanbanEventTransportError(
                code,
                status_code=exc.code,
                retryable=exc.code not in {400, 403, 404},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if self._closed.is_set():
                raise KanbanEventTransportError(
                    "kanban_event_transport_closed",
                    retryable=False,
                ) from exc
            raise KanbanEventTransportError(
                "kanban_event_transport_unavailable",
                status_code=503,
            ) from exc
        finally:
            with self._response_lock:
                self._active_response = None
        if len(raw) > self._max_response_bytes:
            raise KanbanEventContractError("kanban_event_response_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KanbanEventContractError("kanban_event_response_invalid") from exc
        if not isinstance(payload, Mapping):
            raise KanbanEventContractError("kanban_event_response_invalid")
        return KanbanEventBatch.from_mapping(
            payload,
            after_sequence=sequence,
            max_events=self._max_events,
        )

    def _read_one_sse_delivery(self, response: Any) -> bytes:
        chunks: list[bytes] = []
        total = 0
        has_data = False
        while not self._closed.is_set():
            line = response.readline(self._max_event_bytes + 2)
            if not line:
                break
            total += len(line)
            if total > self._max_response_bytes:
                raise KanbanEventContractError("kanban_event_response_too_large")
            if len(line) > self._max_event_bytes + 1:
                raise KanbanEventContractError("kanban_event_frame_too_large")
            chunks.append(line)
            if line.startswith(b"data:"):
                has_data = True
            if has_data and line in {b"\n", b"\r\n"}:
                break
        if self._closed.is_set():
            raise KanbanEventTransportError(
                "kanban_event_transport_closed",
                retryable=False,
            )
        return b"".join(chunks)


def parse_sse_payload(
    raw: bytes,
    *,
    after_sequence: int,
    max_events: int,
    max_event_bytes: int,
) -> KanbanEventBatch:
    deliveries: list[tuple[str, bytes]] = []
    event_id = ""
    data_lines: list[bytes] = []
    event_size = 0
    for line in raw.splitlines():
        if not line:
            if data_lines:
                deliveries.append((event_id, b"\n".join(data_lines)))
            event_id = ""
            data_lines = []
            event_size = 0
            continue
        if line.startswith(b":"):
            continue
        field, _, value = line.partition(b":")
        value = value[1:] if value.startswith(b" ") else value
        if field == b"id":
            event_id = value.decode("utf-8", errors="strict")
        elif field == b"data":
            event_size += len(value)
            if event_size > max_event_bytes:
                raise KanbanEventContractError("kanban_event_frame_too_large")
            data_lines.append(value)
    if data_lines:
        deliveries.append((event_id, b"\n".join(data_lines)))
    if not deliveries or len(deliveries) > max_events:
        raise KanbanEventContractError("kanban_event_sse_delivery_invalid")

    direct_events: list[Mapping[str, Any]] = []
    batch_mapping: Mapping[str, Any] | None = None
    for delivery_id, payload_bytes in deliveries:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KanbanEventContractError("kanban_event_sse_data_invalid") from exc
        if not isinstance(payload, Mapping):
            raise KanbanEventContractError("kanban_event_sse_data_invalid")
        source = payload.get("data", payload)
        if not isinstance(source, Mapping):
            raise KanbanEventContractError("kanban_event_sse_data_invalid")
        if "events" in source or source.get("snapshot_required") is True:
            if batch_mapping is not None or direct_events:
                raise KanbanEventContractError("kanban_event_sse_delivery_ambiguous")
            batch_mapping = source
            continue
        event_mapping = dict(source)
        if delivery_id and "event_id" not in event_mapping:
            event_mapping["event_id"] = delivery_id
        direct_events.append(event_mapping)

    if batch_mapping is not None:
        return KanbanEventBatch.from_mapping(
            batch_mapping,
            after_sequence=after_sequence,
            max_events=max_events,
        )
    latest = after_sequence
    for event in direct_events:
        latest = max(
            latest,
            _strict_sequence(
                event.get("sequence"),
                code="kanban_event_sequence_invalid",
            ),
        )
    return KanbanEventBatch.from_mapping(
        {
            "events": direct_events,
            "next_after_sequence": latest,
            "latest_sequence": latest,
            "has_more": False,
        },
        after_sequence=after_sequence,
        max_events=max_events,
    )


def _strict_sequence(value: Any, *, code: str) -> int:
    if isinstance(value, bool):
        raise KanbanEventContractError(code)
    try:
        sequence = int(value)
    except (TypeError, ValueError) as exc:
        raise KanbanEventContractError(code) from exc
    if sequence < 0:
        raise KanbanEventContractError(code)
    return sequence


def _http_error_code(raw: bytes, status_code: int) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"kanban_event_http_{status_code}"
    if not isinstance(payload, Mapping):
        return f"kanban_event_http_{status_code}"
    nested = payload.get("error")
    if isinstance(nested, Mapping) and nested.get("code"):
        return str(nested["code"])
    return f"kanban_event_http_{status_code}"
