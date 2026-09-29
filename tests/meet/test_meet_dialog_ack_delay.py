"""The paired acknowledgement fault is owned, one-shot and fully observable."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Page

from tests.meet_dialog_ack_delay import PROFILE, DialogAckDelay
from tests.meet_dialog_cadence_delay import install_cadence_delay, require_cadence_profile, start_cadence_observation
from worker.meet_media.screen_frame_delivery import BrowserScreenFrames

URL = "https://synthetic.test/machine"


def test_only_one_owned_ack_and_its_second_idle_are_delayed(monkeypatch):
    begin, wait = Mock(return_value="sent"), Mock(return_value="waited")
    monkeypatch.setattr(BrowserScreenFrames, "begin", begin)
    monkeypatch.setattr(Page, "wait_for_timeout", wait)
    delay = DialogAckDelay(monkeypatch, url=URL)
    page = SimpleNamespace(url=URL)
    other = SimpleNamespace(url=URL + "/other")
    frames = SimpleNamespace(page=page)
    BrowserScreenFrames.begin(SimpleNamespace(page=other), 2, 3, "jpeg")
    BrowserScreenFrames.begin(frames, 2, 2, "jpeg")
    BrowserScreenFrames.begin(frames, 2, 3, "jpeg")
    assert not wait.called
    start_cadence_observation(None)
    start_cadence_observation(delay)
    assert BrowserScreenFrames.begin(frames, 2, 3, "jpeg") == "sent"
    assert [c.args[1] for c in wait.call_args_list] == [350]
    Page.wait_for_timeout(other, 100)
    Page.wait_for_timeout(page, True)
    Page.wait_for_timeout(page, 100.0)
    assert delay.report()["completed_idle_steps"] == 0
    wait.reset_mock()
    for _ in range(5):
        assert Page.wait_for_timeout(page, 100) == "waited"
    BrowserScreenFrames.begin(frames, 2, 4, "jpeg")
    assert [c.args[1] for c in wait.call_args_list] == [100, 350, 100, 100, 100]
    delay.require_complete()
    assert delay.report() == {
        "profile": PROFILE,
        "attempted_ack_calls": 1,
        "completed_ack_delays": 1,
        "completed_idle_steps": 2,
        "completed_idle_delays": 1,
        "production_release_evidence": False,
    }


@pytest.mark.parametrize("fail_at", ["begin", "ack", "first_idle", "second_idle"])
def test_failed_operations_retain_their_error_and_never_count_as_complete(monkeypatch, fail_at):
    failure = RuntimeError("synthetic")
    begin, wait = Mock(), Mock()
    monkeypatch.setattr(BrowserScreenFrames, "begin", begin)
    monkeypatch.setattr(Page, "wait_for_timeout", wait)
    delay = DialogAckDelay(monkeypatch, url=URL)
    delay.start_observation()
    page = SimpleNamespace(url=URL)
    if fail_at == "begin":
        begin.side_effect = failure
    elif fail_at == "ack":
        wait.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        BrowserScreenFrames.begin(SimpleNamespace(page=page), 2, 3, "jpeg")
        if fail_at == "second_idle":
            Page.wait_for_timeout(page, 100)
        wait.side_effect = failure
        Page.wait_for_timeout(page, 100)
    assert caught.value is failure
    with pytest.raises(AssertionError, match="not_fully_exercised"):
        delay.require_complete()


@pytest.mark.parametrize(
    "seconds,spoken,gpu", [(0, False, False), (7200, False, False), (300, True, False), (300, False, True)]
)
def test_ack_fault_cannot_enter_short_long_or_gpu_profiles(seconds, spoken, gpu):
    with pytest.raises(ValueError, match="profile_invalid"):
        require_cadence_profile(PROFILE, soak_seconds=seconds, spoken=spoken, gpu=gpu)


def test_profile_dispatch_is_explicit_and_disabled_path_is_passive(monkeypatch):
    begin, wait = BrowserScreenFrames.begin, Page.wait_for_timeout
    assert install_cadence_delay(False, monkeypatch, url=URL, profile=PROFILE) is None
    assert BrowserScreenFrames.begin is begin and Page.wait_for_timeout is wait
    assert require_cadence_profile(PROFILE, soak_seconds=300, spoken=False, gpu=False)
    assert isinstance(install_cadence_delay(True, monkeypatch, url=URL, profile=PROFILE), DialogAckDelay)


def test_runner_selects_exact_ack_reference_without_sframe_instrumentation():
    from scripts.meet_test_gate_profiles import select_profile

    profile = select_profile("private-dialog-ack-delay")
    ordinary = select_profile("private-dialog-soak-smoke")
    assert profile.node == ordinary.node and profile.image_inputs == ordinary.image_inputs
    assert profile.timeout_seconds == 660 and profile.reference != ordinary.reference
    assert profile.environment()["MEET_DIALOG_SOAK_SECONDS"] == "300"
    assert profile.environment()["MEET_DIALOG_CADENCE_DELAY"] == PROFILE
    assert profile.environment()["MEET_TEST_SFRAME_PIPELINE_PROBE"] == "0"
