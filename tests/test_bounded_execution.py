"""Wall-clock bounds keep a hung handler from holding the hub's dispatch slot."""

from __future__ import annotations

import time

import pytest

from agent.services.bounded_execution import WallTimeout, run_wall_bounded


def test_run_wall_bounded_returns_the_result():
    assert run_wall_bounded(lambda: 42, timeout_seconds=5, label="unit") == 42


def test_run_wall_bounded_raises_after_the_budget():
    with pytest.raises(WallTimeout, match="unit exceeded 0.05s"):
        run_wall_bounded(lambda: time.sleep(0.5), timeout_seconds=0.05, label="unit")
