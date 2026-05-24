from __future__ import annotations

import time

from agent.services.planning_singleflight_service import PlanningSingleFlightService


def test_singleflight_blocks_duplicate_until_release():
    svc = PlanningSingleFlightService()
    assert svc.acquire(goal_id="g-1", ttl_seconds=60) is True
    assert svc.acquire(goal_id="g-1", ttl_seconds=60) is False
    svc.release(goal_id="g-1")
    assert svc.acquire(goal_id="g-1", ttl_seconds=60) is True


def test_singleflight_expires_ttl():
    svc = PlanningSingleFlightService()
    assert svc.acquire(goal_id="g-ttl", ttl_seconds=30) is True
    svc._leases["g-ttl"] = time.time() - 1  # expire lease deterministically in test
    assert svc.acquire(goal_id="g-ttl", ttl_seconds=30) is True
