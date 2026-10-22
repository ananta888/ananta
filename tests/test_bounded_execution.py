"""Wall-clock bounds keep a hung handler from holding the hub's dispatch slot."""

from __future__ import annotations

import contextvars
import time

import pytest

from agent.services.bounded_execution import WallTimeout, run_wall_bounded


def test_run_wall_bounded_returns_the_result():
    assert run_wall_bounded(lambda: 42, timeout_seconds=5, label="unit") == 42


def test_run_wall_bounded_carries_the_callers_context():
    # Capability scopes and request context live in contextvars; a bare thread
    # would lose them and the analysis observations came back empty.
    var: contextvars.ContextVar[str] = contextvars.ContextVar("scope", default="lost")
    var.set("bound")

    def read_scope() -> str:
        return var.get()

    assert run_wall_bounded(read_scope, timeout_seconds=5, label="unit") == "bound"


def test_run_wall_bounded_raises_after_the_budget():
    with pytest.raises(WallTimeout, match="unit exceeded 0.05s"):
        run_wall_bounded(lambda: time.sleep(0.5), timeout_seconds=0.05, label="unit")
