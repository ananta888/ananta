"""The optional private outage fixture cannot mutate or silently skip its checks."""

from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from tests.meet_multi_worker_control_recovery import MultiWorkerControlRecovery


def test_disabled_fixture_does_not_wrap_read_or_report_recovery():
    fixture = MultiWorkerControlRecovery(False)
    native, report = Mock(), Mock()
    assert fixture.wrap(native) is native
    fixture.require(report)
    report.assert_not_called()
    native.assert_not_called()


def test_each_private_worker_gets_one_503_and_must_recover_through_real_port():
    fixture = MultiWorkerControlRecovery(True)
    result, report = object(), Mock()
    native = Mock(return_value=result)
    exchange = fixture.wrap(native)
    for task in ("first", "second"):
        payload = {"task_id": task, "private": "never retained in observations"}
        assert exchange(payload) is result
        with pytest.raises(MeetError) as error:
            exchange(payload)
        assert error.value.status == 503
        assert exchange(payload) is result
    fixture.require(report)
    assert native.call_count == 4
    assert report.call_args.args[1]["fresh_signed_recoveries"] == 2
    assert "private" not in str(report.call_args)
    with pytest.raises(ValueError, match="scope_exceeded"):
        exchange({"task_id": "third"})


def test_failed_native_read_never_counts_as_recovery():
    fixture = MultiWorkerControlRecovery(True)
    exchange = fixture.wrap(Mock(side_effect=MeetError("synthetic_policy_denied", 403)))
    for _ in range(3):
        with pytest.raises(MeetError):
            exchange({"task_id": "first"})
    assert fixture.interrupted == {"first"} and not fixture.recovered
    with pytest.raises(AssertionError):
        fixture.require(Mock())
