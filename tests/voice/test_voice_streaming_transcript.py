from __future__ import annotations

from voice_runtime.streaming import StreamingTranscriptTracker


def test_live_partial_is_emitted_immediately_and_final_exactly_once() -> None:
    tracker = StreamingTranscriptTracker(live_partials=True)
    partial = tracker.ingest(turn_id="turn-1", revision=1, text="Hallo", final=False, observed_at_ms=100)
    assert partial and partial.emitted_at_ms == 100
    final = tracker.ingest(
        turn_id="turn-1", revision=2, text="Hallo Welt", final=True, observed_at_ms=220, segment_closed=True
    )
    assert final and final.final and final.segment_closed
    assert tracker.ingest(turn_id="turn-1", revision=3, text="duplicate", final=True, observed_at_ms=230) is None
    assert tracker.snapshot()["finalized"] == 1


def test_segment_mode_buffers_partials_and_reorder_reconnect_does_not_lose_final() -> None:
    tracker = StreamingTranscriptTracker(live_partials=False, max_turns=2)
    assert tracker.ingest(turn_id="turn-a", revision=1, text="a", final=False, observed_at_ms=0) is None
    assert tracker.ingest(turn_id="turn-a", revision=1, text="a", final=False, observed_at_ms=1) is None
    final = tracker.ingest(turn_id="turn-a", revision=2, text="a final", final=True, observed_at_ms=2)
    assert final and final.text == "a final"
    assert tracker.ingest(turn_id="turn-a", revision=1, text="stale", final=False, observed_at_ms=3) is None
    assert tracker.snapshot()["drops"] == {"stale": 0, "duplicate": 1, "after_final": 1}


def test_long_speech_turn_history_is_bounded_without_timers() -> None:
    tracker = StreamingTranscriptTracker(live_partials=True, max_turns=8)
    for index in range(100):
        tracker.ingest(turn_id=f"turn-{index}", revision=1, text="word " * 100, final=True, observed_at_ms=index)
    snapshot = tracker.snapshot()
    assert len(snapshot["turns"]) == 8 and snapshot["timers"] == 0
