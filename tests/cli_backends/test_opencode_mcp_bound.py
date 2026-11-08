"""The MCP enter must never hold the opencode backend permit unbounded."""

from __future__ import annotations

import contextlib
import time

from agent.cli_backends.opencode import _bounded_mcp


def test_fast_context_passes_through_and_exits():
    events = []

    @contextlib.contextmanager
    def fast():
        events.append("enter")
        yield "endpoint"
        events.append("exit")

    with _bounded_mcp(fast(), seconds=5.0) as ctx:
        assert ctx == "endpoint"
    assert events == ["enter", "exit"]


def test_hanging_enter_degrades_to_no_mcp_within_the_bound():
    @contextlib.contextmanager
    def hang():
        time.sleep(60)
        yield "never"

    started = time.monotonic()
    with _bounded_mcp(hang(), seconds=0.3) as ctx:
        assert ctx is None
    assert time.monotonic() - started < 5.0


def test_failing_enter_degrades_to_no_mcp():
    @contextlib.contextmanager
    def boom():
        raise RuntimeError("serve failed")
        yield  # pragma: no cover

    with _bounded_mcp(boom(), seconds=5.0) as ctx:
        assert ctx is None


def test_nullcontext_protocol_path_is_not_flagged_degraded(caplog):
    # nullcontext enters immediately with None: not a degradation, same as before.
    with caplog.at_level("WARNING"):
        with _bounded_mcp(contextlib.nullcontext(None), seconds=5.0) as ctx:
            assert ctx is None
    assert "opencode_mcp_enter_degraded" not in caplog.text
