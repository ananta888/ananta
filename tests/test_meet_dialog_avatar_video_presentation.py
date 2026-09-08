"""Single fetch/generation slot across video/image/neutral changes, no browser evidence."""

import copy

import pytest

from tests.test_meet_avatar_video_contract import video_assignment
from tests.test_meet_dialog_avatar_presentation import scenario, update


def setup():
    s = scenario()
    s.f.assigned["avatar_videos"] = True
    s.video = video_assignment()
    s.video["reference"].update(tenant_id="tenant", project_id="project")
    s.projection.update(mode="persona-video-v1", reference=s.video["reference"], repeat_mode="loop")
    s.f.browser.start_video.side_effect = lambda *_, **__: s.f.snapshot["receipt"].update(profile="persona-video-v1")
    return s


def complete(s):
    s.pool.calls[-1][0].set_result(copy.deepcopy(s.video))


def test_video_is_consumed_only_on_fresh_hub_update_and_never_by_tick():
    s = setup()
    update(s)
    assert s.pool.calls[0][1] == (s.hub.avatar_video, s.projection["binding"], s.video["reference"], "loop")
    complete(s)
    s.source.tick()
    s.f.browser.start_video.assert_not_called()
    update(s)
    s.f.browser.start_video.assert_called_once_with("avatar:session", s.video, tenant_id="tenant", project_id="project")
    assert s.source.pump.active
    s.f.browser.start_image.assert_not_called()
    s.f.browser.start.assert_not_called()
    s.f.elapsed[0] += 2.5
    s.source.tick()
    assert not s.source.pump.active
    s.source.close()


def test_stale_pending_video_cannot_activate_after_switch_to_image_or_grow_queue():
    s = setup()
    update(s)
    for revision in range(2, 12):
        s.projection["binding"]["avatar_revision"] = revision
        s.f.controls["avatar"]["revision"] = revision
        update(s)
    assert len(s.pool.calls) == 1
    s.projection.update(mode="persona-image-v1", reference=s.image["reference"])
    del s.projection["repeat_mode"]
    complete(s)
    update(s)
    assert len(s.pool.calls) == 2 and s.pool.calls[-1][1][0] == s.hub.avatar_image
    s.f.browser.start_video.assert_not_called()
    s.pool.calls[-1][0].set_result(copy.deepcopy(s.image))
    update(s)
    s.f.browser.start_image.assert_called_once()
    s.source.close()


@pytest.mark.parametrize("change", ["paused", "blocked", "epoch", "repeat"])
def test_source_revocation_or_changed_generation_never_releases_old_video(change):
    s = setup()
    update(s)
    complete(s)
    if change in ("paused", "blocked"):
        s.projection.update(state=change, binding=None, reference=None)
        update(s)
    elif change == "epoch":
        s.f.authority["membershipEpoch"] += 1
        with pytest.raises(ValueError):
            update(s)
    else:
        s.projection["repeat_mode"] = "hold_last"
        update(s)
    s.f.browser.start_video.assert_not_called()
    s.source.close()


def test_old_image_assignment_rejects_new_video_projection_without_fetch():
    s = setup()
    del s.f.assigned["avatar_videos"]
    with pytest.raises(ValueError):
        update(s)
    assert not s.pool.calls
    s.f.browser.start_video.assert_not_called()
    s.source.close()
