"""Deterministic Hub freshness and browser boundaries, not decoded-media evidence."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_avatar_source import validate_avatar_snapshot
from tests.test_meet_dialog_transport import assignment
from worker.meet_media.avatar_browser import AvatarBrowserPort
from worker.meet_media.dialog_avatar_pump import DialogAvatarPump

NOW = 1788000000


def fixture():
    assigned = assignment() | {"deadline": NOW + 600, "capabilities": ["avatar.publish"]}
    elapsed = [100.0]
    page = Mock(url=assigned["meeting"]["origin"] + "/machine")
    source = {"state": "open", "generation": 1, "frames": 1, "expiresAt": (NOW + 30) * 1000}
    receipt = {
        "schema": "ananta.meet-avatar-source.v1",
        "profile": "neutral-ai-v1",
        "generation": 1,
        "width": 256,
        "height": 256,
        "fps": 5,
        "heartbeatMs": 2500,
        "expiresAt": source["expiresAt"],
    }
    snapshot = {"phase": "done", "generation": 1, "receipt": receipt, "source": source}
    browser = Mock()
    browser.status.side_effect = lambda: copy.deepcopy(snapshot)
    authority = {
        "lease": {"sessionId": "ms_test", "generation": 1, "expiresAt": (NOW + 60) * 1000},
        "peerId": "machine",
        "roomId": assigned["meeting"]["room_id"],
        "membershipEpoch": 1,
    }
    controls = {"avatar": {"enabled": True, "revision": 1, "since": NOW * 1000}}
    def clock():
        return NOW + elapsed[0] - 100
    pump = DialogAvatarPump(page, assigned, browser=browser, clock=clock, monotonic=lambda: elapsed[0])
    return SimpleNamespace(**locals())


def test_only_fresh_hub_updates_pulse_and_ticks_never_authorize():
    f = fixture()
    f.pump.tick()
    f.browser.start.assert_not_called()
    f.pump.update(f.authority, f.controls)
    f.browser.start.assert_called_once_with("avatar:session")
    for _ in range(10):
        f.pump.tick()
    f.browser.pulse.assert_called_once()
    f.elapsed[0] += 2
    f.pump.update(f.authority, f.controls)
    assert f.browser.pulse.call_count == 2 and f.pump.active and not f.pump.failed


@pytest.mark.parametrize("change", ["paused", "missing", "capability", "navigation", "stale", "expired", "malformed"])
def test_stop_failure_and_freshness_remove_only_owned_avatar(change):
    f = fixture()
    f.pump.update(f.authority, f.controls)
    if change == "paused":
        f.controls["avatar"]["enabled"] = False
    elif change == "missing":
        f.controls.clear()
    elif change == "capability":
        f.assigned["capabilities"].clear()
    elif change == "navigation":
        f.page.url += "?changed"
    elif change in {"stale", "expired"}:
        f.elapsed[0] += 3 if change == "stale" else 601
    else:
        f.snapshot["receipt"]["profile"] = "https://external-image"
    if change in {"paused", "missing", "capability"}:
        f.pump.update(f.authority, f.controls)
    else:
        f.pump.tick()
    assert not f.pump.active
    starts = f.browser.start.call_count
    for _ in range(3):
        f.pump.tick()
    assert f.browser.start.call_count == starts
    assert f.browser.pulse.call_count == 1
    f.page.evaluate.assert_not_called()  # No direct screen/speech/chat mutation.


@pytest.mark.parametrize("change", ["generation", "sessionId", "membership", "peer", "control"])
def test_new_hub_scope_replaces_instead_of_pulsing_old_generation(change):
    f = fixture()
    f.pump.update(f.authority, f.controls)
    if change == "generation":
        f.authority["lease"]["generation"] += 1
    elif change == "sessionId":
        f.authority["lease"]["sessionId"] = "ms_other"
    elif change == "membership":
        f.authority["membershipEpoch"] += 1
    elif change == "peer":
        f.authority["peerId"] = "replacement"
    else:
        f.controls["avatar"]["revision"] += 1
    f.pump.update(f.authority, f.controls)
    assert f.browser.start.call_count == 2 and f.browser.close.call_count >= 2


def test_graph_failure_requires_changed_hub_control_and_cleanup_failure_is_bounded():
    f = fixture()
    f.browser.start.side_effect = ValueError("unsupported")
    f.browser.close.side_effect = None
    f.pump.update(f.authority, f.controls)
    assert f.pump.failed
    f.pump.update(f.authority, f.controls)
    f.browser.start.assert_called_once()
    f.browser.start.side_effect = None
    f.controls["avatar"]["revision"] += 1
    f.pump.update(f.authority, f.controls)
    assert f.pump.active
    f.browser.close.side_effect = ValueError("target closed")
    f.pump.close()
    assert not f.pump.active and f.pump.failed


def test_source_expiry_waits_for_next_fresh_hub_update_and_keeps_no_self_renewal_loop():
    f = fixture()
    f.snapshot["source"]["expiresAt"] = f.snapshot["receipt"]["expiresAt"] = (NOW + 1) * 1000
    f.pump.update(f.authority, f.controls)
    f.elapsed[0] += 1.1
    f.pump.tick()
    assert not f.pump.active and not f.pump.failed
    f.pump.tick()
    f.browser.start.assert_called_once()
    f.snapshot["source"]["expiresAt"] = f.snapshot["receipt"]["expiresAt"] = (NOW + 30) * 1000
    f.pump.update(f.authority, f.controls)
    assert f.browser.start.call_count == 2 and f.pump.active


def test_late_hub_response_discards_the_old_controller_generation():
    f = fixture()
    f.pump.update(f.authority, f.controls)
    f.elapsed[0] += 3
    f.pump.update(f.authority, f.controls)
    assert f.browser.start.call_count == 2 and f.pump.active


def test_pending_setup_keeps_main_loop_free_but_has_a_ten_second_limit():
    f = fixture()
    f.snapshot.update(phase="pending", receipt=None)
    f.snapshot["source"].update(state="opening", frames=0)
    f.pump.update(f.authority, f.controls)
    for _ in range(4):
        f.elapsed[0] += 2
        f.pump.update(f.authority, f.controls)
    assert f.pump.active and f.browser.start.call_count == 1
    f.elapsed[0] += 2
    f.pump.update(f.authority, f.controls)
    assert not f.pump.active and f.pump.failed


@pytest.mark.parametrize(
    "patch",
    [
        {"generation": True},
        {"phase": "stale"},
        {"receipt": None},
        {"unknown": True},
        {"source": {"state": "open", "generation": 2, "frames": 1, "expiresAt": (NOW + 30) * 1000}},
    ],
)
def test_closed_snapshot_rejects_extra_fields_and_inconsistent_generations(patch):
    f = fixture()
    with pytest.raises(ValueError):
        validate_avatar_snapshot(f.snapshot | patch, NOW * 1000, (NOW + 60) * 1000)


def test_browser_bridge_has_no_blocking_setup_or_automatic_pulse_and_pins_navigation():
    page = Mock(url="https://meet.example.test/machine")
    port = AvatarBrowserPort(page, page.url)
    port.start("avatar:session")
    script, args = page.evaluate.call_args.args
    assert "setInterval" not in script and "neutral-ai-v1" in script
    assert "generation !== before" in script and "void Promise.resolve" in script
    token = args[0]
    with pytest.raises(ValueError, match="busy"):
        port.start("avatar:session")
    port.pulse()
    assert page.evaluate.call_args.args[1] == token
    port.close()
    assert "avatar.close(phase.generation)" in page.evaluate.call_args.args[0]
    port.start("avatar:session")
    page.url += "?changed"
    with pytest.raises(ValueError, match="navigation"):
        port.pulse()
    calls = page.evaluate.call_count
    port.close()
    assert page.evaluate.call_count == calls
