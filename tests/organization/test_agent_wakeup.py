"""PAO: hub-native wakeup requests with coalescing and idempotency."""

from __future__ import annotations

import pytest

from agent.services.agent_wakeup import WakeupQueue


def queue():
    clock = {"now": 0.0}
    return WakeupQueue(clock=lambda: clock["now"]), clock


def test_enqueue_creates_a_queued_request():
    q, _ = queue()
    request = q.enqueue("agent-1", "schedule", reason="daily")
    assert request.status == "queued" and request.coalesced_count == 0
    assert q.pending(agent_id="agent-1") == [request]


def test_repeated_triggers_coalesce_into_one_pending_request():
    q, _ = queue()
    first = q.enqueue("agent-1", "event", trigger_detail="a")
    second = q.enqueue("agent-1", "event", trigger_detail="b")
    assert second.request_id == first.request_id and second.coalesced_count == 1
    assert len(q.pending(agent_id="agent-1")) == 1


def test_different_source_does_not_coalesce():
    q, _ = queue()
    a = q.enqueue("agent-1", "event")
    b = q.enqueue("agent-1", "schedule")
    assert a.request_id != b.request_id


def test_idempotency_key_returns_the_same_request():
    q, _ = queue()
    first = q.enqueue("agent-1", "event", idempotency_key="k-1")
    q.claim(agent_id="agent-1")  # even after claim, the key still resolves
    again = q.enqueue("agent-1", "event", idempotency_key="k-1")
    assert again.request_id == first.request_id


def test_claim_and_finish_lifecycle():
    q, clock = queue()
    request = q.enqueue("agent-1", "assignment")
    clock["now"] = 5.0
    claimed = q.claim(agent_id="agent-1")
    assert claimed.status == "claimed" and claimed.claimed_at == 5.0
    clock["now"] = 9.0
    finished = q.finish(request.request_id)
    assert finished.status == "finished" and finished.finished_at == 9.0
    assert q.pending() == []


def test_finish_with_error_marks_failed_and_unknown_is_rejected():
    q, _ = queue()
    request = q.enqueue("agent-1", "manual")
    assert q.finish(request.request_id, error="boom").status == "failed"
    with pytest.raises(ValueError, match="wakeup_request_unknown"):
        q.finish("missing")


@pytest.mark.parametrize("agent_id,source", [("", "event"), ("agent", "bogus")])
def test_invalid_wakeups_are_rejected(agent_id, source):
    q, _ = queue()
    with pytest.raises(ValueError):
        q.enqueue(agent_id, source)
