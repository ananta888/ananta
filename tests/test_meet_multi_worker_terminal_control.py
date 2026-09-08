"""The negative packaged-control fixture is explicit, scoped and observational."""

from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from tests.meet_multi_worker_terminal_control import MultiWorkerTerminalControl


def test_disabled_terminal_fixture_does_not_wrap_or_act():
    fixture, native, report = MultiWorkerTerminalControl(False), Mock(), Mock()
    assert fixture.wrap(native) is native
    fixture.wait_stopped(None, None)
    fixture.require(report)
    report.assert_not_called()
    with pytest.raises(ValueError, match="scope_invalid"):
        fixture.arm("task")


def test_terminal_denial_only_after_arming_exact_task_and_never_silently_allows_retry():
    fixture, result, report = MultiWorkerTerminalControl(True), object(), Mock()
    native = Mock(return_value=result)
    exchange = fixture.wrap(native)
    assert exchange({"task_id": "first"}) is result
    fixture.arm("first")
    assert exchange({"task_id": "second"}) is result
    with pytest.raises(MeetError) as error:
        exchange({"task_id": "first"})
    assert error.value.code == "meet_authorization_contract_invalid" and error.value.status == 502
    fixture.require(report)
    assert report.call_args.args[1]["retries"] == 0 and native.call_count == 2
    with pytest.raises(MeetError):
        exchange({"task_id": "first"})
    with pytest.raises(AssertionError, match="read retries"):
        fixture.require(report)
    with pytest.raises(ValueError, match="scope_invalid"):
        fixture.arm("second")


def test_unarmed_fixture_cannot_claim_terminal_proof():
    with pytest.raises(AssertionError):
        MultiWorkerTerminalControl(True).require(Mock())
