"""Headless image fetch/source composition; no automatic policy or media claims."""

import copy
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_avatar_image_bridge import image_assignment
from tests.test_meet_dialog_avatar_pump import NOW, fixture
from worker.meet_media.dialog_avatar_presentation import DialogAvatarPresentation


class ManualPool:
    def __init__(self):
        self.calls = []
        self.shutdown = Mock()

    def submit(self, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.calls.append((future, args))
        return future


def scenario():
    f = fixture()
    f.assigned["avatar_images"] = True
    f.authority["lease"]["sessionId"] = "ms_" + "a" * 32
    image = image_assignment()
    image["reference"].update(tenant_id="tenant", project_id="project")
    binding = {
        name: f.assigned[name]
        for name in ("tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id")
    }
    binding |= {
        "room_id": f.authority["roomId"],
        "meet_session_id": f.authority["lease"]["sessionId"],
        "own_peer_id": "machine",
        "generation": 1,
        "membership_epoch": 1,
        "avatar_revision": 1,
        "deadline_ms": (NOW + 60) * 1000,
        "selection_digest": "a" * 64,
    }
    projection = {"mode": "persona-image-v1", "state": "ready", "reference": image["reference"], "binding": binding}
    pool, hub = ManualPool(), Mock()
    f.browser.start_image.side_effect = lambda *_, **__: f.snapshot["receipt"].update(profile="persona-image-v1")
    f.browser.start.side_effect = lambda *_: f.snapshot["receipt"].update(profile="neutral-ai-v1")
    source = DialogAvatarPresentation(
        f.page, hub, f.assigned, browser=f.browser, pool=pool, clock=f.clock, monotonic=lambda: f.elapsed[0]
    )
    return SimpleNamespace(**locals())


def update(s):
    s.source.update(s.f.authority, s.f.controls, s.projection)


def complete(s, value=None):
    s.pool.calls[-1][0].set_result(copy.deepcopy(s.image if value is None else value))


def test_only_fresh_hub_update_can_consume_image_start_source_or_pulse():
    s = scenario()
    update(s)
    assert len(s.pool.calls) == 1
    s.f.browser.start_image.assert_not_called()
    complete(s)
    s.source.tick()
    s.source.tick()
    s.f.browser.start_image.assert_not_called()
    update(s)
    s.f.browser.start_image.assert_called_once_with("avatar:session", s.image, tenant_id="tenant", project_id="project")
    s.f.browser.start.assert_not_called()
    assert s.f.browser.pulse.call_count == 1
    for _ in range(5):
        s.source.tick()
    assert s.f.browser.pulse.call_count == 1
    s.source.close()
    s.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_stalled_fetch_retains_one_slot_through_many_selection_changes_and_drops_old_image():
    s = scenario()
    update(s)
    for revision in range(2, 22):
        s.f.controls["avatar"]["revision"] = revision
        s.projection["binding"]["avatar_revision"] = revision
        update(s)
    assert len(s.pool.calls) == 1
    assert s.pool.calls[0][1][1]["avatar_revision"] == 1
    complete(s)
    update(s)
    assert len(s.pool.calls) == 2
    s.f.browser.start_image.assert_not_called()
    complete(s)
    update(s)
    s.f.browser.start_image.assert_called_once()
    s.source.close()


@pytest.mark.parametrize("mode", ["paused", "blocked", "control"])
def test_pause_or_denial_closes_only_avatar_and_never_opens_neutral(mode):
    s = scenario()
    update(s)
    complete(s)
    update(s)
    if mode == "control":
        s.f.controls["avatar"]["enabled"] = False
    else:
        s.projection.update(state=mode, binding=None, reference=None)
    update(s)
    assert s.source.pump is None
    s.f.browser.close.assert_called()
    s.f.browser.start.assert_not_called()
    s.f.page.evaluate.assert_not_called()
    assert len(s.pool.calls) == 1
    s.source.close()


def test_explicit_neutral_switch_does_not_publish_a_late_image_or_retain_finished_bytes():
    s = scenario()
    update(s)
    s.projection = {"mode": "neutral-ai-v1", "state": "ready", "binding": None, "reference": None}
    update(s)
    s.f.browser.start.assert_called_once()
    complete(s)
    update(s)
    s.f.browser.start_image.assert_not_called()
    assert s.source.pending is None and len(s.pool.calls) == 1
    s.source.close()


def test_source_expires_on_stale_hub_even_with_completed_cached_image():
    s = scenario()
    update(s)
    complete(s)
    update(s)
    calls = s.f.browser.pulse.call_count
    s.f.elapsed[0] += 3
    s.source.tick()
    assert s.source.pump.active is False
    assert s.f.browser.pulse.call_count == calls
    s.source.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "foreign"),
        ("runtime_id", "foreign"),
        ("own_peer_id", "other"),
        ("generation", 2),
        ("avatar_revision", 2),
        ("deadline_ms", (NOW + 61) * 1000),
    ],
)
def test_mismatched_hub_meet_or_source_binding_never_fetches(field, value):
    s = scenario()
    s.projection["binding"][field] = value
    with pytest.raises(ValueError, match="authority_changed"):
        update(s)
    assert not s.pool.calls
    s.f.browser.start_image.assert_not_called()
    s.source.close()


def test_failed_fetch_is_not_retried_without_changed_authority():
    s = scenario()
    update(s)
    s.pool.calls[0][0].set_exception(ValueError("revoked"))
    update(s)
    for _ in range(10):
        update(s)
    assert len(s.pool.calls) == 1
    s.f.browser.start_image.assert_not_called()
    s.f.browser.start.assert_not_called()
    s.f.controls["avatar"]["revision"] = s.projection["binding"]["avatar_revision"] = 2
    update(s)
    assert len(s.pool.calls) == 2
    s.source.close()


def test_navigation_or_close_prevents_any_future_activation_without_blocking_on_fetch():
    s = scenario()
    update(s)
    s.f.page.url = "https://foreign.test/machine"
    with pytest.raises(ValueError, match="scope_changed"):
        update(s)
    s.source.close()
    s.source.close()
    complete(s)
    s.source.tick()
    with pytest.raises(ValueError, match="closed"):
        update(s)
    s.f.browser.start_image.assert_not_called()
    s.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_image_presentation_cannot_be_instantiated_for_unnegotiated_assignment():
    s = scenario()
    s.f.assigned.pop("avatar_images")
    with pytest.raises(ValueError, match="not_negotiated"):
        DialogAvatarPresentation(s.f.page, s.hub, s.f.assigned)
    s.source.close()
