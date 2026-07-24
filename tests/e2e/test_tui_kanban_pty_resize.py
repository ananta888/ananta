from __future__ import annotations

import sys

import pytest

from scripts.e2e.tui_kanban_pty_resize import (
    DEFAULT_TERMINAL_SIZES,
    LOCAL_SCOPE,
    run_pty_resize_diagnostic,
)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires Linux PTY and /proc",
)
def test_real_tui_process_redraws_at_required_terminal_sizes() -> None:
    report = run_pty_resize_diagnostic(
        card_count=1000,
        timeout_seconds=15.0,
    )

    assert report["scope"] == LOCAL_SCOPE
    assert report["release_evidence"] is False
    assert report["formal_gate_eligible"] is False
    assert report["card_count"] == 1000
    assert [
        (item["columns"], item["rows"])
        for item in report["terminal_sizes"]
    ] == list(DEFAULT_TERMINAL_SIZES)
    assert all(
        measurement["marker_present"]
        and measurement["process_alive"]
        and measurement["redraw_latency_ms"] >= 0
        for measurement in report["resize_measurements"]
    )
    assert report["peak_rss_kib"] > 0
