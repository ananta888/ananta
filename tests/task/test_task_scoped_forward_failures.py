"""No response at all from a worker is a transport outcome, not an empty payload.

``_normalize_forwarded_step_envelope(None)`` yields ``{}``, so a missing response used to be
reported as ``worker_empty_payload``: a permanent-looking symptom that hid the real cause (the
read budget expiring while the governed step deadline still allowed the propose) and that the
retry policy could not tell apart from a worker that really returned nothing.
"""

from __future__ import annotations

import pytest

from agent.services import _task_scoped_forwarding as forwarding
from agent.services._task_scoped_forward_failures import _raise_if_no_worker_response
from agent.services.worker_forward_transport import (
    WorkerForwardAmbiguousTransportError,
    WorkerForwardDeadlineExceeded,
    WorkerTransportDeadline,
)


def test_the_forwarder_seams_the_guard_to_its_forward_path():
    assert forwarding._raise_if_no_worker_response is _raise_if_no_worker_response


def test_a_worker_response_is_left_for_the_envelope_to_normalize():
    assert _raise_if_no_worker_response({"status": "ok", "data": {}}, transport_deadline=None) is None
    assert _raise_if_no_worker_response({"status": "error"}, transport_deadline=None) is None


def test_a_missing_response_is_a_retryable_transport_failure():
    deadline = WorkerTransportDeadline.after_seconds(600)

    with pytest.raises(WorkerForwardAmbiguousTransportError) as exc:
        _raise_if_no_worker_response(None, transport_deadline=deadline)

    assert exc.value.retryable is True
    assert exc.value.reason_code == "worker_forward_ambiguous_response_loss"


def test_a_missing_response_is_not_retried_beyond_the_governed_budget():
    clock = {"now": 0.0}
    deadline = WorkerTransportDeadline.after_seconds(10, monotonic_clock=lambda: clock["now"])
    clock["now"] = 10.0

    with pytest.raises(WorkerForwardDeadlineExceeded) as exc:
        _raise_if_no_worker_response(None, transport_deadline=deadline)

    assert exc.value.retryable is False
    assert exc.value.reason_code == "worker_forward_transport_deadline_exceeded"
