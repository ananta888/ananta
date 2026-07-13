"""Runtime-neutral authenticated Hub stream client for CLI and TUI consumers."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Protocol

from agent.services.workflow_runtime.streaming import (
    WORKFLOW_STREAM_FRAME_SCHEMA,
    WORKFLOW_STREAM_REQUEST_SCHEMA,
)


class WorkflowStreamClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowStreamHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class WorkflowStreamHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> WorkflowStreamHttpResponse: ...

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> WorkflowStreamHttpResponse: ...


class UrllibWorkflowStreamTransport:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> WorkflowStreamHttpResponse:
        request = urllib.request.Request(url, method="GET", headers=dict(headers))
        return self._open(request, timeout_seconds=timeout_seconds)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> WorkflowStreamHttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=dict(headers),
        )
        return self._open(request, timeout_seconds=timeout_seconds)

    @staticmethod
    def _open(
        request: urllib.request.Request,
        *,
        timeout_seconds: float,
    ) -> WorkflowStreamHttpResponse:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                payload = response.read(1_048_577)
                if len(payload) > 1_048_576:
                    raise WorkflowStreamClientError("workflow_stream_response_too_large")
                return WorkflowStreamHttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=payload,
                )
        except urllib.error.HTTPError as exc:
            raise WorkflowStreamClientError(f"workflow_stream_http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise WorkflowStreamClientError("workflow_stream_connection_failed") from exc


class WorkflowStreamClient:
    """One implementation shared by CLI/TUI; cancellation stops future polls."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        transport: WorkflowStreamHttpTransport | None = None,
        timeout_seconds: float = 35.0,
    ) -> None:
        self._hub_url = str(hub_url).rstrip("/")
        self._token = str(bearer_token).strip()
        self._transport = transport or UrllibWorkflowStreamTransport()
        self._timeout_seconds = float(timeout_seconds)
        if not self._hub_url.startswith(("http://", "https://")):
            raise WorkflowStreamClientError("workflow_stream_hub_url_invalid")
        if not self._token:
            raise WorkflowStreamClientError("workflow_stream_auth_required")
        if not 1 <= self._timeout_seconds <= 120:
            raise WorkflowStreamClientError("workflow_stream_timeout_invalid")

    def read_page(
        self,
        workflow_id: str,
        *,
        after_cursor: str = "",
        max_events: int = 128,
        heartbeat_seconds: float = 15,
    ) -> tuple[tuple[dict[str, Any], ...], str, bool]:
        command = {
            "schema": WORKFLOW_STREAM_REQUEST_SCHEMA,
            "workflow_id": str(workflow_id),
            "after_cursor": str(after_cursor),
            "max_events": int(max_events),
            "heartbeat_seconds": float(heartbeat_seconds),
        }
        body = json.dumps(
            command,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        response = self._transport.post(
            f"{self._hub_url}/api/visual-process/workflow/events/stream",
            headers={
                "Accept": "application/x-ndjson",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        if response.status != 200:
            raise WorkflowStreamClientError(f"workflow_stream_http_{response.status}")
        frames = tuple(
            self._parse_frame(line, workflow_id=str(workflow_id)) for line in response.body.splitlines() if line
        )
        cursor = _header(response.headers, "X-Workflow-Next-Cursor") or after_cursor
        if _cursor_offset(cursor) < _cursor_offset(after_cursor):
            raise WorkflowStreamClientError("workflow_stream_cursor_regressed")
        has_more = _header(response.headers, "X-Workflow-Has-More").lower() == "true"
        return frames, cursor, has_more

    def capabilities(
        self,
        *,
        required_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Read the same runtime-neutral Hub projection used by Angular."""

        required = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in required_capabilities
                    if str(value).strip()
                }
            )
        )
        if len(required) > 64 or any(len(value) > 128 for value in required):
            raise WorkflowStreamClientError("runtime_capability_query_invalid")
        query = urllib.parse.urlencode(
            [("required_capability", value) for value in required]
        )
        url = f"{self._hub_url}/api/workflow-runtime/capabilities"
        if query:
            url = f"{url}?{query}"
        response = self._transport.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            timeout_seconds=self._timeout_seconds,
        )
        if response.status != 200:
            raise WorkflowStreamClientError(f"runtime_capability_http_{response.status}")
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowStreamClientError("runtime_capability_response_invalid") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema") != "ananta.workflow_runtime_capability_matrix.v1"
            or not isinstance(value.get("runtimes"), list)
        ):
            raise WorkflowStreamClientError("runtime_capability_response_invalid")
        return value

    def cancel(self, workflow_id: str, *, reason: str = "client_cancelled") -> None:
        if not str(workflow_id).strip() or len(str(workflow_id)) > 160:
            raise WorkflowStreamClientError("workflow_stream_workflow_id_invalid")
        if len(str(reason)) > 1000:
            raise WorkflowStreamClientError("workflow_cancel_reason_too_long")
        body = json.dumps(
            {"reason": str(reason)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        response = self._transport.post(
            f"{self._hub_url}/api/visual-process/workflow/"
            f"{urllib.parse.quote(str(workflow_id), safe='')}/cancel",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        if not 200 <= response.status < 300:
            raise WorkflowStreamClientError(f"workflow_cancel_http_{response.status}")

    def iter_frames(
        self,
        workflow_id: str,
        *,
        after_cursor: str = "",
        cancelled: Callable[[], bool] = lambda: False,
        maximum_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        cursor = after_cursor
        pages = 0
        seen_event_ids: set[str] = set()
        while not cancelled():
            frames, cursor, has_more = self.read_page(workflow_id, after_cursor=cursor)
            for frame in frames:
                event_id = str(frame.get("event_id") or "")
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
                if len(seen_event_ids) > 8192:
                    raise WorkflowStreamClientError("workflow_stream_dedupe_window_exceeded")
                yield frame
            pages += 1
            if maximum_pages is not None and pages >= maximum_pages:
                return
            if not has_more:
                return

    @staticmethod
    def _parse_frame(raw: bytes, *, workflow_id: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowStreamClientError("workflow_stream_frame_invalid") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema") != WORKFLOW_STREAM_FRAME_SCHEMA
            or value.get("workflow_id") != workflow_id
            or not isinstance(value.get("payload"), dict)
            or not str(value.get("event_type") or "")
            or not str(value.get("cursor") or "")
        ):
            raise WorkflowStreamClientError("workflow_stream_frame_invalid")
        return value


def _header(headers: Mapping[str, str], name: str) -> str:
    expected = name.lower()
    return next((str(value) for key, value in headers.items() if str(key).lower() == expected), "")


def _cursor_offset(cursor: str) -> int:
    if not cursor:
        return 0
    prefix, separator, raw_offset = str(cursor).partition(":")
    if prefix != "v1" or separator != ":" or not raw_offset.isdigit():
        raise WorkflowStreamClientError("workflow_stream_cursor_invalid")
    return int(raw_offset)


__all__ = [
    "UrllibWorkflowStreamTransport",
    "WorkflowStreamClient",
    "WorkflowStreamClientError",
    "WorkflowStreamHttpResponse",
    "WorkflowStreamHttpTransport",
]
