"""Progress cache bounds IPC work without caching execution authorization."""

from unittest.mock import Mock

import pytest

from worker.meet_media.companion_browser_progress import CompanionBrowserProgress
from worker.meet_media.companion_chat_pump import CompanionSessionEnded


def test_repeated_frame_checkpoints_share_only_recent_browser_progress():
    now = [0]
    progress = CompanionBrowserProgress(clock=lambda: now[0])
    poll, pulse = Mock(return_value={"joined": True}), Mock()
    for milliseconds in (0, 20, 40, 60, 80):
        now[0] = milliseconds / 1000
        assert progress.step(poll=poll, pulse=pulse) == {"joined": True}
    assert poll.call_count == pulse.call_count == 1
    now[0] = .1
    progress.step(poll=poll, pulse=pulse)
    assert poll.call_count == pulse.call_count == 2


def test_membership_loss_at_next_poll_propagates_and_discards_cached_progress():
    now = [0]
    progress = CompanionBrowserProgress(clock=lambda: now[0])
    poll, pulse = Mock(return_value={"joined": True}), Mock()
    progress.step(poll=poll, pulse=pulse)
    now[0] = .1
    poll.side_effect = CompanionSessionEnded("synthetic_membership_lost")
    with pytest.raises(CompanionSessionEnded):
        progress.step(poll=poll, pulse=pulse)
    assert progress.state is None


def test_slow_poll_does_not_add_its_latency_to_next_refresh():
    now = [0]
    progress = CompanionBrowserProgress(clock=lambda: now[0])
    def poll():
        now[0] += .2
        return {"joined": True}
    pulse = Mock()
    progress.step(poll=poll, pulse=pulse)
    progress.step(poll=poll, pulse=pulse)
    assert pulse.call_count == 2
