from __future__ import annotations

import time

import pytest

from agent.services.worker_forward_transport import (
    WorkerForwardDeadlineExceeded,
    WorkerForwardPermanentTransportError,
    WorkerTransportDeadline,
    invoke_worker_forwarder,
)


def test_legacy_forwarder_remains_compatible_without_governed_deadline():
    calls = []

    def legacy(url, endpoint, data, token=None):
        calls.append((url, endpoint, data, token))
        return {"status": "success"}

    result = invoke_worker_forwarder(
        legacy,
        "http://worker:5001",
        "/tasks/public-v1/step/execute",
        {"task_id": "public-v1"},
        token="token",
        transport_deadline=None,
    )

    assert result == {"status": "success"}
    assert len(calls) == 1


def test_governed_deadline_never_downgrades_to_legacy_forwarder():
    called = False

    def legacy(_url, _endpoint, _data, token=None):
        nonlocal called
        called = True

    with pytest.raises(
        WorkerForwardPermanentTransportError,
        match="worker_forward_deadline_transport_unsupported",
    ):
        invoke_worker_forwarder(
            legacy,
            "http://worker:5001",
            "/tasks/v2/step/execute",
            {"task_id": "v2"},
            token="token",
            transport_deadline=(
                WorkerTransportDeadline.after_seconds(60)
            ),
        )

    assert called is False


def test_deadline_aware_forwarder_receives_same_absolute_deadline():
    received = []

    def forwarder(_url, _endpoint, _data, token=None, **options):
        received.append((token, options["transport_deadline"]))
        return {"status": "success"}

    deadline = WorkerTransportDeadline.after_seconds(60)
    result = invoke_worker_forwarder(
        forwarder,
        "http://worker:5001",
        "/tasks/v2/step/execute",
        {"task_id": "v2"},
        token="token",
        transport_deadline=deadline,
    )

    assert result == {"status": "success"}
    assert received == [("token", deadline)]


def test_expired_deadline_is_permanent_before_adapter_call():
    called = False

    def forwarder(_url, _endpoint, _data, token=None, **_options):
        nonlocal called
        called = True

    deadline = WorkerTransportDeadline(
        expires_at_monotonic=time.monotonic() - 1,
        budget_seconds=1,
    )

    with pytest.raises(WorkerForwardDeadlineExceeded):
        invoke_worker_forwarder(
            forwarder,
            "http://worker:5001",
            "/tasks/v2/step/execute",
            {"task_id": "v2"},
            token="token",
            transport_deadline=deadline,
        )

    assert called is False


def test_deadline_keeps_the_clock_used_by_its_factory():
    now = [100.0]
    deadline = WorkerTransportDeadline.after_seconds(
        5,
        monotonic_clock=lambda: now[0],
    )

    assert deadline.require_remaining_seconds() == 5.0
    now[0] = 104.25
    assert deadline.remaining_seconds() == 0.75
    now[0] = 105.0

    with pytest.raises(WorkerForwardDeadlineExceeded):
        deadline.require_remaining_seconds()
