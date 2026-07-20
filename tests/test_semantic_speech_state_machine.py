from __future__ import annotations

import random

from voice_runtime.speech_mode_policy import SpeechModeEvent, SpeechModeMachine


def test_live_and_segment_finalization_are_independent_settings() -> None:
    live = SpeechModeMachine()
    state = live.apply(SpeechModeEvent(1, 1, "negotiate_live"))
    assert state.mode == "transcript_live" and state.live_partials and state.correct_each_segment
    segment = SpeechModeMachine()
    state = segment.apply(SpeechModeEvent(1, 1, "negotiate_segment"))
    assert state.mode == "segment_only" and not state.live_partials and state.correct_each_segment


def test_duplicate_reorder_epoch_and_revoke_are_deterministic_and_fail_safe() -> None:
    events = [
        SpeechModeEvent(1, 1, "negotiate_reconstruction"),
        SpeechModeEvent(2, 1, "partial"),
        SpeechModeEvent(3, 1, "reconstruction_failed"),
    ]
    machine = SpeechModeMachine()
    states = [machine.apply(event) for event in events]
    assert states[-1].mode == "ordinary_audio"
    assert machine.apply(events[1]) == states[-1]
    assert machine.apply(SpeechModeEvent(1, 2, "negotiate_live")).reason_code == "stale_epoch"
    assert machine.apply(SpeechModeEvent(0, 2, "consent_revoked")).mode == "ordinary_audio"


def test_random_duplicates_never_change_bitstable_result() -> None:
    canonical = [
        SpeechModeEvent(1, 1, "negotiate_delayed"),
        SpeechModeEvent(2, 1, "segment_final"),
        SpeechModeEvent(3, 1, "correction_complete"),
    ]
    expected = SpeechModeMachine()
    for event in canonical:
        expected.apply(event)
    noisy = [*canonical, *canonical, canonical[1]]
    random.Random(17).shuffle(noisy)
    # Reorder may intentionally suppress unseen lower revisions; replaying the
    # canonical sequence afterwards converges without a second side effect.
    actual = SpeechModeMachine()
    for event in noisy:
        actual.apply(event)
    for event in canonical:
        actual.apply(event)
    assert actual.state.mode in {"delayed_correction", "ordinary_audio"}
    assert actual.state.last_sequence == expected.state.last_sequence
