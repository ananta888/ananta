"""Only the chosen owned page and bounded idle phase receive real test delays."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Page

from tests.meet_dialog_cadence_delay import PROFILE, DialogCadenceDelay, require_cadence_profile


def test_delay_is_bounded_to_twenty_completed_waits_on_the_exact_owned_page(monkeypatch):
    wait = Mock(return_value="completed")
    monkeypatch.setattr(Page, "wait_for_timeout", wait)
    observer = DialogCadenceDelay(monkeypatch, url="https://synthetic.test/machine")
    owned = SimpleNamespace(url="https://synthetic.test/machine")
    other = SimpleNamespace(url="https://synthetic.test/")
    for timeout in (20, 50, True, 100.0):
        assert Page.wait_for_timeout(owned, timeout) == "completed"
    Page.wait_for_timeout(other, 100)
    assert observer.report()["attempted_idle_calls"] == 0
    wait.reset_mock()
    for _ in range(35):
        Page.wait_for_timeout(owned, 100)
    assert [call.args[1] for call in wait.call_args_list] == [450, 450, 100] * 10 + [100] * 5
    observer.require_complete()
    assert observer.report() == {
        "profile": PROFILE,
        "attempted_idle_calls": 30,
        "completed_delays": 20,
        "production_release_evidence": False,
    }
    copied = observer.report()
    copied["completed_delays"] = 99
    assert observer.report()["completed_delays"] == 20


def test_failed_wait_is_not_a_completed_delay_and_original_error_survives(monkeypatch):
    failure = RuntimeError("synthetic")
    monkeypatch.setattr(Page, "wait_for_timeout", Mock(side_effect=failure))
    observer = DialogCadenceDelay(monkeypatch, url="https://synthetic.test/machine")
    with pytest.raises(RuntimeError) as caught:
        Page.wait_for_timeout(SimpleNamespace(url="https://synthetic.test/machine"), 100)
    assert caught.value is failure
    assert observer.report()["completed_delays"] == 0
    with pytest.raises(AssertionError, match="not_fully_exercised"):
        observer.require_complete()


@pytest.mark.parametrize(
    "profile,seconds,spoken,gpu",
    [
        ("arbitrary", 300, False, False),
        (PROFILE, 0, False, False),
        (PROFILE, 7200, False, False),
        (PROFILE, 300, True, False),
        (PROFILE, 300, False, True),
    ],
)
def test_cadence_fault_cannot_enter_other_reference_profiles(profile, seconds, spoken, gpu):
    with pytest.raises(ValueError, match="profile_invalid"):
        require_cadence_profile(profile, soak_seconds=seconds, spoken=spoken, gpu=gpu)


def test_off_is_passive_and_only_exact_five_minute_cpu_profile_can_activate():
    assert not require_cadence_profile("off", soak_seconds=7200, spoken=False, gpu=False)
    assert require_cadence_profile(PROFILE, soak_seconds=300, spoken=False, gpu=False)


def test_disabled_hooks_do_not_patch_browser_or_emit_fault_observations(monkeypatch):
    from tests.meet_dialog_cadence_delay import install_cadence_delay, record_cadence_delay, require_cadence_complete

    original, record = Page.wait_for_timeout, Mock()
    delay = install_cadence_delay(False, monkeypatch, url="https://synthetic.test/machine")
    assert delay is None and Page.wait_for_timeout is original
    require_cadence_complete(delay)
    record_cadence_delay(delay, record)
    record.assert_not_called()
