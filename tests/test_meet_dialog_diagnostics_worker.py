"""No sampling loop, secret formatting, outcome replacement or hidden retry."""

from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_diagnostics import DialogRunDiagnostics, ProcessUsage, stop_reason


def collector(**kwargs):
    return DialogRunDiagnostics(
        enabled=True,
        clock=Mock(side_effect=[100, 105]),
        usage=Mock(side_effect=[ProcessUsage(2, 3, 400, 500), ProcessUsage(4, 6, 600, 700)]),
        **kwargs,
    )


def test_one_observation_distinguishes_cpu_deltas_and_nonaggregate_rss_peaks():
    measured = collector()
    send = Mock(return_value=True)
    assert measured.finish(send, failure=ValueError("meet_dialog_control_state_stale"))
    value = send.call_args.args[0]
    assert value["stop_reason"] == "control_stale"
    assert value["measurements"] == {
        "elapsed_ms": 5000,
        "self_cpu_ms": 2000,
        "terminated_children_cpu_ms": 3000,
        "self_max_rss_kib": 600,
        "terminated_child_max_rss_kib": 700,
    }
    assert not measured.finish(send, completed=True)
    send.assert_called_once()


def test_disabled_measurement_never_reads_clock_resources_or_sends():
    clock, usage, send = Mock(), Mock(), Mock()
    measured = DialogRunDiagnostics(clock=clock, usage=usage)
    assert not measured.finish(send, completed=True)
    clock.assert_not_called()
    usage.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize("stage", ["start", "end", "clock", "negative", "nan", "rss"])
def test_unavailable_or_invalid_measurements_remain_null_not_zero_or_success(stage):
    clock = Mock(side_effect=[100, 99 if stage == "clock" else 101])
    normal = ProcessUsage(2, 3, 400, 500)
    values = [normal, normal]
    if stage in {"start", "end"}:
        values[0 if stage == "start" else 1] = OSError("private")
    elif stage == "negative":
        values[1] = ProcessUsage(1, 3, 400, 500)
    elif stage == "nan":
        values[1] = ProcessUsage(float("nan"), 3, 400, 500)
    elif stage == "rss":
        values[1] = ProcessUsage(2, 3, True, 500)
    measured = DialogRunDiagnostics(enabled=True, clock=clock, usage=Mock(side_effect=values))
    send = Mock(return_value=True)
    assert measured.finish(send)
    assert send.call_args.args[0]["measurements"] is None


@pytest.mark.parametrize(
    "code,expected",
    [
        ("meet_dialog_control_request_stale", "control_stale"),
        ("meet_dialog_hub_revoked_or_unavailable", "hub_unavailable_or_revoked"),
        ("meet_dialog_session_operation_failed", "session_failed"),
        ("secret token in error", "runtime_failed"),
    ],
)
def test_only_closed_builtin_error_codes_are_classified(code, expected):
    assert stop_reason(False, ValueError(code)) == expected
    assert stop_reason(True, None) == "assignment_elapsed"


def test_foreign_error_formatting_is_never_invoked_and_reporting_cannot_replace_failure():
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("must not format")

    assert stop_reason(False, PrivateError()) == "runtime_failed"
    error = ValueError("original")
    measured = collector()
    send = Mock(side_effect=OSError("private transport"))
    with pytest.raises(ValueError) as raised:
        try:
            raise error
        finally:
            assert not measured.finish(send, failure=error)
    assert raised.value is error
    assert not measured.finish(send, failure=error)
    send.assert_called_once()
