"""Soak observations preserve calls/exceptions and never retain source content."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_soak_observer import DialogSoakObserver
from worker.meet_media.dialog_screen_pump import DialogScreenPump


@pytest.mark.parametrize("fails", [False, True])
def test_bounded_screen_cadence_observer_preserves_exact_pump_behavior(monkeypatch, fails):
    error = RuntimeError("PRIVATE")
    tick = Mock(side_effect=error if fails else None, return_value="actual")
    monkeypatch.setattr(DialogScreenPump, "tick", tick)
    for name in ("DialogRpcObserver", "DialogControlObserver", "DialogTransportObserver", "DialogBrowserCostObserver"):
        monkeypatch.setattr("tests.meet_dialog_soak_observer." + name, Mock())
    now = [100.0]
    observer = DialogSoakObserver(monkeypatch, clock=lambda: now[0])
    pump = SimpleNamespace(sequence=9, frames=SimpleNamespace(busy=True), failed=False, content="PRIVATE")
    for _ in range(20):
        now[0] += 0.1
        if fails:
            with pytest.raises(RuntimeError) as caught:
                DialogScreenPump.tick(pump)
            assert caught.value is error
        else:
            assert DialogScreenPump.tick(pump) == "actual"
    tick.assert_called_with(pump)
    rows = observer.report()["screen_ticks"]
    assert len(rows) == 16 and all(row["gap_ms"] == 100 for row in rows)
    assert rows[-1] == {
        "at_ms": 102000,
        "gap_ms": 100,
        "duration_ms": 0,
        "sequence": 9,
        "pending": True,
        "failed": False,
    }
    rows[-1]["sequence"] = 99
    assert observer.report()["screen_ticks"][-1]["sequence"] == 9
