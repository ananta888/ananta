"""Native test diagnostics preserve real failure and reject arbitrary content."""

from unittest.mock import Mock

import pytest

from tests.meet_media_timing_observer import observe_media_timing_failure
from tests.test_meet_media_timing_contract import fixture
from worker.meet_media.media_timing_gate import MediaTimingGate


def test_failure_is_retained_once_without_changing_the_fence(monkeypatch):
    record = Mock()
    observe_media_timing_failure(monkeypatch, record)
    gate = MediaTimingGate(1, {"speech"})
    value = fixture()
    assert gate.accept(value) == value
    record.assert_not_called()
    value["sources"]["speech"]["state"] = "failed"
    with pytest.raises(ValueError, match="source_failed"):
        gate.accept(value)
    assert gate.closed
    record.assert_called_once_with("dialog_media_timing_failure", value)
    value["sources"]["speech"]["generation"] = 2
    assert record.call_args.args[1]["sources"]["speech"]["generation"] == 1
    with pytest.raises(ValueError, match="closed"):
        gate.accept(value)
    assert record.call_count == 1


@pytest.mark.parametrize("value", [None, {"credential": "PRIVATE"}, fixture() | {"extra": "PRIVATE"}])
def test_invalid_snapshot_never_emits_input_or_exception_text(monkeypatch, value):
    record = Mock()
    observe_media_timing_failure(monkeypatch, record)
    with pytest.raises(ValueError):
        MediaTimingGate(1, {"speech"}).accept(value)
    record.assert_called_once_with("dialog_media_timing_failure", {"state": "invalid_snapshot"})
