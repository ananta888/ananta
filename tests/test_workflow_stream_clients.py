from __future__ import annotations

import json

import pytest

from agent.cli.workflow_stream_client import WorkflowStreamClient as CliStreamClient
from agent.sdk.workflow_stream import (
    WorkflowStreamClient,
    WorkflowStreamClientError,
    WorkflowStreamHttpResponse,
)
from agent.tui.workflow_stream_client import WorkflowStreamClient as TuiStreamClient


class _Transport:
    def __init__(self, response: WorkflowStreamHttpResponse) -> None:
        self.response = response
        self.calls = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls.append((url, headers, body, timeout_seconds))
        return self.response

    def get(self, url, *, headers, timeout_seconds):
        self.calls.append((url, headers, None, timeout_seconds))
        return self.response


class _SequenceTransport:
    def __init__(self, responses: list[WorkflowStreamHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls.append((url, headers, body, timeout_seconds))
        return self.responses.pop(0)

    def get(self, url, *, headers, timeout_seconds):
        self.calls.append((url, headers, None, timeout_seconds))
        return self.responses.pop(0)


def _frame(**overrides):
    value = {
        "schema": "ananta.workflow_stream_frame.v1",
        "event_type": "workflow.node.started",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "step_id": "step-1",
        "cursor": "v1:1",
        "event_id": "event-1",
        "occurred_at": 100,
        "payload": {},
    }
    value.update(overrides)
    return value


def test_cli_and_tui_share_one_authenticated_post_client() -> None:
    assert CliStreamClient is WorkflowStreamClient
    assert TuiStreamClient is WorkflowStreamClient
    transport = _Transport(
        WorkflowStreamHttpResponse(
            status=200,
            headers={"X-Workflow-Next-Cursor": "v1:1", "X-Workflow-Has-More": "false"},
            body=(json.dumps(_frame()) + "\n").encode(),
        )
    )
    client = WorkflowStreamClient(
        hub_url="https://hub.test/",
        bearer_token="user-token",
        transport=transport,
    )

    frames, cursor, has_more = client.read_page("workflow-1", after_cursor="v1:0")

    assert frames == (_frame(),)
    assert cursor == "v1:1"
    assert has_more is False
    url, headers, body, _timeout = transport.calls[0]
    assert url == "https://hub.test/api/visual-process/workflow/events/stream"
    assert "?" not in url
    assert headers["Authorization"] == "Bearer user-token"
    assert json.loads(body)["after_cursor"] == "v1:0"


def test_stream_client_rejects_cross_workflow_frame() -> None:
    transport = _Transport(
        WorkflowStreamHttpResponse(
            status=200,
            headers={},
            body=json.dumps(_frame(workflow_id="workflow-foreign")).encode(),
        )
    )
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )

    with pytest.raises(WorkflowStreamClientError, match="frame_invalid"):
        client.read_page("workflow-1")


def test_stream_iteration_honors_disconnect_before_next_page() -> None:
    transport = _Transport(
        WorkflowStreamHttpResponse(
            status=200,
            headers={"X-Workflow-Next-Cursor": "v1:1", "X-Workflow-Has-More": "true"},
            body=json.dumps(_frame()).encode(),
        )
    )
    cancelled = False
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )
    iterator = client.iter_frames("workflow-1", cancelled=lambda: cancelled)

    assert next(iterator) == _frame()
    cancelled = True
    assert list(iterator) == []
    assert len(transport.calls) == 1


def test_stream_iteration_deduplicates_replayed_frame_after_reconnect() -> None:
    transport = _SequenceTransport(
        [
            WorkflowStreamHttpResponse(
                status=200,
                headers={"X-Workflow-Next-Cursor": "v1:1", "X-Workflow-Has-More": "true"},
                body=json.dumps(_frame()).encode(),
            ),
            WorkflowStreamHttpResponse(
                status=200,
                headers={"X-Workflow-Next-Cursor": "v1:2", "X-Workflow-Has-More": "false"},
                body=(
                    json.dumps(_frame())
                    + "\n"
                    + json.dumps(_frame(cursor="v1:2", event_id="event-2"))
                ).encode(),
            ),
        ]
    )
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )

    frames = list(client.iter_frames("workflow-1"))

    assert [frame["event_id"] for frame in frames] == ["event-1", "event-2"]


def test_stream_client_rejects_cursor_regression() -> None:
    transport = _Transport(
        WorkflowStreamHttpResponse(
            status=200,
            headers={"X-Workflow-Next-Cursor": "v1:1"},
            body=b"",
        )
    )
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )

    with pytest.raises(WorkflowStreamClientError, match="cursor_regressed"):
        client.read_page("workflow-1", after_cursor="v1:2")


def test_stream_client_cancel_uses_authenticated_post_body() -> None:
    transport = _Transport(WorkflowStreamHttpResponse(status=200, headers={}, body=b"{}"))
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )

    client.cancel("workflow-1", reason="operator_cancelled")

    url, headers, body, _timeout = transport.calls[0]
    assert url == "https://hub.test/api/visual-process/workflow/workflow-1/cancel"
    assert headers["Authorization"] == "Bearer user-token"
    assert json.loads(body) == {"reason": "operator_cancelled"}


def test_cli_tui_client_reads_authenticated_shared_capability_projection() -> None:
    projection = {
        "schema": "ananta.workflow_runtime_capability_matrix.v1",
        "matrix_version": "1.0.0",
        "required_capabilities": ["durability"],
        "runtimes": [{"runtime_id": "temporal"}],
    }
    transport = _Transport(
        WorkflowStreamHttpResponse(
            status=200,
            headers={},
            body=json.dumps(projection).encode(),
        )
    )
    client = WorkflowStreamClient(
        hub_url="https://hub.test",
        bearer_token="user-token",
        transport=transport,
    )

    assert client.capabilities(required_capabilities=("durability",)) == projection
    url, headers, body, _timeout = transport.calls[0]
    assert url == (
        "https://hub.test/api/workflow-runtime/capabilities"
        "?required_capability=durability"
    )
    assert headers["Authorization"] == "Bearer user-token"
    assert body is None
