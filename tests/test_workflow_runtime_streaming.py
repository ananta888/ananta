from __future__ import annotations

import json

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.visual_process import vp_bp
from agent.services.workflow_route_authorization_service import workflow_route_authorization_service
from agent.services.workflow_runtime.streaming import (
    WORKFLOW_STREAM_FRAME_SCHEMA,
    WORKFLOW_STREAM_REQUEST_SCHEMA,
    WorkflowStreamError,
    WorkflowStreamRequest,
    WorkflowStreamService,
)


class _AdmittedTestReleaseEvidence:
    def evaluate(self, **_values):
        return True, "runtime_release_test_evidence_verified"


class _History:
    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def list_workflow_events(self, _workflow_id: str) -> list[dict]:
        return list(self.events)


class _SequencedHistory:
    def __init__(self, snapshots: list[list[dict]]) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    def list_workflow_events(self, _workflow_id: str) -> list[dict]:
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return list(self.snapshots[index])


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _request(**overrides) -> WorkflowStreamRequest:
    raw = {
        "schema": WORKFLOW_STREAM_REQUEST_SCHEMA,
        "workflow_id": "workflow-1",
        "max_events": 2,
    }
    raw.update(overrides)
    return WorkflowStreamRequest.from_mapping(raw)


def test_stream_maps_canonical_types_redacts_payloads_and_applies_backpressure() -> None:
    history = _History(
        [
            {
                "event_id": "event-1",
                "event_type": "step_started",
                "timestamp": 100,
                "details": {"step_id": "step-1", "api_key": "do-not-stream"},
            },
            {
                "event_id": "event-2",
                "event_type": "tool_completed",
                "timestamp": 101,
                "details": {"tool": "read_file"},
            },
            {"event_id": "event-3", "event_type": "workflow_completed", "timestamp": 102},
        ]
    )

    batch = WorkflowStreamService(history, clock=lambda: 200).read(_request())

    assert batch.next_cursor == "v1:2"
    assert batch.has_more is True
    assert [frame.event_type for frame in batch.frames] == [
        "workflow.node.started",
        "workflow.tool.completed",
        "workflow.stream.backpressure",
    ]
    assert "do-not-stream" not in str(batch.to_dict())
    assert "REDACTED" in str(batch.to_dict())


def test_stream_cursor_resume_and_heartbeat_are_stable() -> None:
    history = _History([{"event_id": "event-1", "event_type": "workflow_started", "timestamp": 100}])
    service = WorkflowStreamService(history, clock=lambda: 200)

    first = service.read(_request(max_events=1))
    resumed = service.read(_request(max_events=1, after_cursor=first.next_cursor))

    assert first.frames[0].schema == WORKFLOW_STREAM_FRAME_SCHEMA
    assert first.next_cursor == "v1:1"
    assert resumed.next_cursor == "v1:1"
    assert resumed.frames[0].event_type == "workflow.stream.heartbeat"


def test_long_poll_returns_new_event_before_heartbeat_deadline() -> None:
    fake_time = _FakeTime()
    history = _SequencedHistory(
        [
            [],
            [],
            [{"event_id": "event-1", "event_type": "workflow_started", "timestamp": 100}],
        ]
    )
    service = WorkflowStreamService(
        history,
        clock=lambda: 200,
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
        poll_interval_seconds=0.1,
    )

    batch = service.read(_request(wait_seconds=5, heartbeat_seconds=2))

    assert batch.frames[0].event_type == "workflow.run.started"
    assert batch.next_cursor == "v1:1"
    assert fake_time.value < 2


def test_long_poll_disconnect_stops_without_reading_or_cancelling_runtime() -> None:
    fake_time = _FakeTime()
    history = _History([])
    service = WorkflowStreamService(
        history,
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
    )

    with pytest.raises(WorkflowStreamError, match="workflow_stream_disconnected"):
        service.read(
            _request(wait_seconds=5),
            disconnected=lambda: fake_time.value >= 0.2,
        )


@pytest.mark.parametrize(
    "override,reason_code",
    [
        ({"after_cursor": "1"}, "workflow_stream_cursor_invalid"),
        ({"max_events": 257}, "workflow_stream_max_events_invalid"),
        ({"wait_seconds": 31}, "workflow_stream_wait_invalid"),
        ({"access_token": "secret"}, "workflow_stream_unknown_field"),
    ],
)
def test_stream_contract_rejects_unbounded_or_secret_bearing_shapes(override, reason_code) -> None:
    with pytest.raises(WorkflowStreamError) as caught:
        _request(**override)

    assert caught.value.reason_code == reason_code


def test_authenticated_hub_stream_uses_post_body_and_returns_resume_cursor(
    monkeypatch,
    workflow_runtime_auth_keyring_file,
) -> None:
    del workflow_runtime_auth_keyring_file
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    # This test owns the authenticated streaming contract. Rollout admission
    # is exercised separately with mandatory Hub-compiled scopes and policies;
    # keep the real release evidence, worker health and Hub control boundary.
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_rollout_policies",
        lambda: None,
    )
    monkeypatch.setattr(
        "agent.services.workflow_control_composition._production_release_admission",
        lambda _backend: _AdmittedTestReleaseEvidence(),
    )
    from agent.services.workflow_control_composition import (
        reset_workflow_backend_control_facade,
    )

    reset_workflow_backend_control_facade()
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    workflow_route_authorization_service.clear()
    token = generate_token(
        {"sub": "stream-owner", "tenant_id": "tenant-stream", "role": "user"},
        settings.secret_key,
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = app.test_client()
    workflow_id = "workflow-stream-api-contract"
    started = client.post(
        "/api/visual-process/workflow/start",
        headers=headers,
        json={
            "workflow_request": {
                "workflow_id": workflow_id,
                "workflow_type": "stream_test",
                "steps": [
                    {
                        "step_id": "step-1",
                        "task_kind": "coding",
                        "policy_scope": {"source": "stream-test"},
                    }
                ],
                "policy_scope": {"source": "stream-test"},
            }
        },
    )
    assert started.status_code == 200, started.get_json()

    rejected_query = client.post(
        "/api/visual-process/workflow/events/stream?workflow_id=leaked",
        headers=headers,
        json={"schema": WORKFLOW_STREAM_REQUEST_SCHEMA, "workflow_id": workflow_id},
    )
    streamed = client.post(
        "/api/visual-process/workflow/events/stream",
        headers=headers,
        json={"schema": WORKFLOW_STREAM_REQUEST_SCHEMA, "workflow_id": workflow_id},
    )

    assert rejected_query.status_code == 400
    assert streamed.status_code == 200
    assert streamed.content_type.startswith("application/x-ndjson")
    frames = [json.loads(line) for line in streamed.text.splitlines()]
    assert frames[0]["event_type"] == "workflow.run.started"
    assert streamed.headers["X-Workflow-Next-Cursor"].startswith("v1:")


def test_stream_auth_expiry_fails_before_history_is_read(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_ORCHESTRATION_BACKEND", "local")
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    expired = generate_token(
        {
            "sub": "expired-stream-owner",
            "tenant_id": "tenant-stream",
            "role": "user",
        },
        settings.secret_key,
        expires_in=-60,
    )

    response = app.test_client().post(
        "/api/visual-process/workflow/events/stream",
        headers={"Authorization": f"Bearer {expired}"},
        json={
            "schema": WORKFLOW_STREAM_REQUEST_SCHEMA,
            "workflow_id": "workflow-never-read",
        },
    )

    assert response.status_code == 401
