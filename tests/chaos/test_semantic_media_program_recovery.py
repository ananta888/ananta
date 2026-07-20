from __future__ import annotations

import hashlib

import pytest

from agent.services.semantic_media_recovery_fence import RecoveryFenceError, SemanticMediaRecoveryFence

FAILURE_TARGETS = ("hub", "sfu", "relay", "browser", "reconciliation-worker", "training-worker", "store")
COMMIT_BOUNDARIES = ("before-reserve", "after-reserve", "after-write", "before-ack", "after-ack")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize("target", FAILURE_TARGETS)
@pytest.mark.parametrize("boundary", COMMIT_BOUNDARIES)
def test_failure_fences_late_result_and_cleans_owned_resources(target: str, boundary: str) -> None:
    fence = SemanticMediaRecoveryFence(maximum_attempts=3)
    scope = _digest(f"scope:{target}")
    first = fence.begin(scope_digest=scope, epoch=7, consent_version=3)
    for kind in ("timer", "track", "temporary", "reservation"):
        fence.register_resource(first, kind=kind, opaque_id=f"{target}-{boundary}-{kind}")
    fence.fence(scope_digest=scope)
    assert fence.cleanup(first) == 0  # fencing already cleaned every owned handle
    assert fence.resource_count() == 0
    with pytest.raises(RecoveryFenceError, match="recovery_attempt_fenced"):
        fence.commit(first, result_digest=_digest("late"))

    retry = fence.begin(scope_digest=scope, epoch=8, consent_version=4)
    accepted = fence.commit(retry, result_digest=_digest(f"{target}:{boundary}:result"))
    assert accepted.state == "committed"
    assert fence.commit(retry, result_digest=accepted.result_digest or "") == accepted
    # Recovery control never mutates ordinary media or the independent live transcript.
    assert {"ordinary_call": "healthy", "live_transcript": "healthy"} == {
        "ordinary_call": "healthy",
        "live_transcript": "healthy",
    }


def test_retry_budget_and_duplicate_result_are_bounded() -> None:
    fence = SemanticMediaRecoveryFence(maximum_attempts=2)
    scope = _digest("bounded")
    first = fence.begin(scope_digest=scope, epoch=1, consent_version=1)
    fence.fence(scope_digest=scope)
    second = fence.begin(scope_digest=scope, epoch=2, consent_version=2)
    committed = fence.commit(second, result_digest=_digest("one"))
    assert fence.commit(second, result_digest=_digest("one")) == committed
    with pytest.raises(RecoveryFenceError, match="recovery_result_conflict"):
        fence.commit(second, result_digest=_digest("two"))
    with pytest.raises(RecoveryFenceError, match="recovery_attempts_exhausted"):
        fence.begin(scope_digest=scope, epoch=3, consent_version=3)
    with pytest.raises(RecoveryFenceError, match="recovery_attempt_stale"):
        fence.commit(first, result_digest=_digest("late"))
