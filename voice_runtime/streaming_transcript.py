"""Monotone projection of streaming partial/final transcript revisions."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranscriptRevisionEvent:
    turn_id: str
    revision: int
    text: str
    authority: str
    final: bool
    emitted_at_ms: int
    segment_closed: bool = False


class StreamingTranscriptTracker:
    """Bounded monotone projection of backend partial/final revisions.

    Segment rotation is deliberately independent from live partial delivery.
    The tracker owns no audio and schedules no correction task.
    """

    def __init__(self, *, live_partials: bool, max_turns: int = 512) -> None:
        if not 1 <= max_turns <= 4096:
            raise ValueError("streaming_transcript_turn_budget_invalid")
        self._live_partials = bool(live_partials)
        self._max_turns = max_turns
        self._latest: dict[str, TranscriptRevisionEvent] = {}
        self._finalized: set[str] = set()
        self._buffered_partial: dict[str, TranscriptRevisionEvent] = {}
        self._drops = {"stale": 0, "duplicate": 0, "after_final": 0}

    def ingest(
        self,
        *,
        turn_id: str,
        revision: int,
        text: str,
        final: bool,
        observed_at_ms: int,
        segment_closed: bool = False,
    ) -> TranscriptRevisionEvent | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", turn_id):
            raise ValueError("streaming_transcript_turn_invalid")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("streaming_transcript_revision_invalid")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65_536:
            raise ValueError("streaming_transcript_text_invalid")
        if turn_id in self._finalized:
            self._drops["after_final"] += 1
            return None
        prior = self._latest.get(turn_id)
        if prior is not None and revision <= prior.revision:
            self._drops["duplicate" if revision == prior.revision else "stale"] += 1
            return None
        event = TranscriptRevisionEvent(
            turn_id=turn_id,
            revision=revision,
            text=text,
            authority="final" if final else "provisional",
            final=bool(final),
            emitted_at_ms=max(0, int(observed_at_ms)),
            segment_closed=bool(segment_closed),
        )
        self._latest[turn_id] = event
        if final:
            self._finalized.add(turn_id)
            self._buffered_partial.pop(turn_id, None)
        elif not self._live_partials:
            self._buffered_partial[turn_id] = event
            self._trim()
            return None
        self._trim()
        return event

    def snapshot(self) -> dict[str, object]:
        return {
            "turns": tuple(self._latest.values()),
            "finalized": len(self._finalized),
            "buffered_partials": len(self._buffered_partial),
            "drops": dict(self._drops),
            "timers": 0,
        }

    def _trim(self) -> None:
        while len(self._latest) > self._max_turns:
            oldest = next(iter(self._latest))
            self._latest.pop(oldest, None)
            self._finalized.discard(oldest)
            self._buffered_partial.pop(oldest, None)
