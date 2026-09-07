"""Bounded observation adapter and fresh-Hub-only pulse composition."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_speech_publication import NOW, Browser
from worker.meet_media.browser_speech_playback import BrowserSpeechPlayback


def setup():
    feeder = Mock()
    feeder.pulse.return_value = True
    authority = Mock(return_value={"url": "https://meet.test/machine"})
    receipt = Browser().open("speech:session", 4410)
    elapsed = [100]
    playback = BrowserSpeechPlayback(
        Mock(),
        "session",
        b"ab" * 4410,
        authority,
        opened_receipt=receipt,
        clock=lambda: NOW,
        monotonic=lambda: elapsed[0],
        feeder=feeder,
    )
    progress = {
        "state": "open",
        "generation": 1,
        "receivedSamples": 4410,
        "playedSamples": 2205,
        "bufferedSamples": 2205,
    }
    feeder.status.side_effect = lambda: {"state": progress["state"], "sent": 4410, "source": copy.deepcopy(progress)}
    return SimpleNamespace(**locals())


def test_progress_poll_never_renews_controller_and_completed_playback_closes_once():
    f = setup()
    f.playback.tick()
    assert (f.playback.sent, f.playback.played, f.playback.completed) == (4410, 2205, False)
    f.feeder.pulse.assert_not_called()
    f.playback.refresh()
    f.feeder.pulse.assert_called_once_with(f.authority.return_value)
    f.progress.update(state="completed", generation=2, playedSamples=4410, bufferedSamples=0)
    f.playback.tick()
    assert f.playback.completed and f.playback.played == 4410
    f.playback.close()
    f.playback.close()
    f.feeder.close.assert_called_once()


@pytest.mark.parametrize("change", ["state", "sent", "received", "played", "queue", "generation", "extra", "null"])
def test_malformed_or_regressed_observation_is_terminal(change):
    f = setup()
    f.playback.tick()
    value = f.feeder.status()
    if change == "state":
        value["state"] = "failed"
    elif change == "sent":
        value["sent"] = True
    elif change == "received":
        value["source"]["receivedSamples"] -= 1
    elif change == "played":
        value["source"].update(playedSamples=0, bufferedSamples=4410)
    elif change == "queue":
        value["source"]["bufferedSamples"] = 4411
    elif change == "generation":
        value["source"]["generation"] += 1
    elif change == "extra":
        value["private"] = "not permitted"
    else:
        value = None
    f.feeder.status.side_effect = None
    f.feeder.status.return_value = value
    with pytest.raises(ValueError):
        f.playback.tick()
    assert f.playback.closed and not f.playback.completed
    f.feeder.close.assert_called_once()


@pytest.mark.parametrize("action", ["tick", "refresh"])
def test_hub_revocation_after_browser_rpc_closes_without_replay(action):
    f = setup()
    f.authority.side_effect = [{"url": "https://meet.test/machine"}, ValueError("revoked")]
    with pytest.raises(ValueError, match="revoked"):
        getattr(f.playback, action)()
    f.feeder.close.assert_called_once()
    assert f.feeder.start.call_count == 1


def test_source_deadline_and_stale_pulse_cannot_extend_playback():
    f = setup()
    f.feeder.pulse.return_value = False
    with pytest.raises(ValueError, match="pulse_denied"):
        f.playback.refresh()
    g = setup()
    g.elapsed[0] += 50
    with pytest.raises(ValueError, match="expired"):
        g.playback.tick()
    g.feeder.status.assert_not_called()


@pytest.mark.parametrize("pcm", [b"", b"a", b"x" * 1764002, bytearray(b"ab"), None])
def test_bad_pcm_never_reaches_browser(pcm):
    feeder = Mock()
    with pytest.raises(ValueError):
        BrowserSpeechPlayback(Mock(), "session", pcm, Mock(), opened_receipt={}, feeder=feeder)
    feeder.start.assert_not_called()


def test_dialog_pulses_only_on_new_hub_updates_and_transfers_pcm_after_validated_opening():
    from tests.test_meet_dialog_speech_opening import setup as output_setup

    f, state = output_setup()
    playback = Mock(completed=False)
    factory = Mock(return_value=playback)
    f.output.playback_factory = factory
    assert f.output.accept(f.result, f.expected)
    factory.assert_not_called()
    f.output.tick()
    state.ready = True
    f.output.tick()
    assert f.output.pcm == b"" and f.output.opening is None
    assert factory.call_args.args[2] == f.result.pcm
    assert not f.browser.frames
    playback.refresh.assert_not_called()
    f.elapsed[0] += 1
    f.output.update(f.receipt, f.controls)
    playback.refresh.assert_called_once()
    authority = factory.call_args.args[3]()
    assert authority["hubUntil"] <= f.output.clock() * 1000 + 2500
    f.output.tick()
    assert playback.refresh.call_count == 1
    f.controls["speech"]["enabled"] = False
    f.output.update(f.receipt, f.controls)
    playback.close.assert_called_once()
    assert not f.output.busy and f.output.binding is None


def test_explicit_synchronous_opening_remains_compatible_without_browser_feeder():
    from tests.test_meet_dialog_speech_output import fixture
    from worker.meet_media.dialog_speech_output import DialogSpeechOutput

    f = fixture()
    output = DialogSpeechOutput(f.page, f.assignment, opening_factory=None)
    assert output.opening_factory is None and output.playback_factory is None
    with pytest.raises(ValueError, match="requires_deferred_opening"):
        DialogSpeechOutput(f.page, f.assignment, opening_factory=None, playback_factory=Mock())
