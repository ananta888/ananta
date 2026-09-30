"""Pure timeline, gap and transcript projections of Voice live runs."""

from __future__ import annotations

import re
import time
from typing import Any, Mapping

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB

_WORD_NORMALIZER = re.compile(r"(^\W+|\W+$)", re.UNICODE)


def timeline_items(
    segments: tuple[VoiceLiveRunSegmentDB, ...],
    result_by_sequence: Mapping[int, Mapping[str, Any]],
    gaps: list[int],
    *,
    gap_timeline_revision: int,
) -> list[dict[str, Any]]:
    items = [
        {
            "id": segment.id,
            "sequence": segment.sequence,
            "status": segment.status,
            "task_id": segment.task_id,
            "result_ref": segment.result_ref,
            "provisional_result_ref": segment.provisional_result_ref,
            "correction_task_id": segment.correction_task_id,
            "correction_status": segment.correction_status,
            "correction_failure_code": segment.correction_failure_code,
            "revision": segment.text_revision,
            "text_revision": segment.text_revision,
            "timeline_revision": segment.timeline_revision,
            "text_state": text_state(segment),
            "started_at_ms": segment.started_at_ms,
            "ended_at_ms": segment.ended_at_ms,
            "duration_ms": segment.duration_ms,
            "overlap_milliseconds": segment.overlap_milliseconds,
            "attempt_count": segment.attempt_count,
            "failure_code": segment.failure_code,
            "correction_started_at": segment.correction_started_at,
            "correction_completed_at": segment.correction_completed_at,
            "text": (result_by_sequence.get(segment.sequence) or {}).get("text"),
            "provider": (result_by_sequence.get(segment.sequence) or {}).get("provider"),
            "model": (result_by_sequence.get(segment.sequence) or {}).get("model"),
        }
        for segment in segments
    ]
    present = {segment.sequence for segment in segments}
    items.extend(
        {
            "id": None,
            "sequence": sequence,
            "status": "gap",
            "task_id": None,
            "result_ref": None,
            "provisional_result_ref": None,
            "correction_task_id": None,
            "correction_status": "not_requested",
            "correction_failure_code": None,
            "revision": 0,
            "text_revision": 0,
            "timeline_revision": gap_timeline_revision,
            "text_state": "none",
            "started_at_ms": None,
            "ended_at_ms": None,
            "duration_ms": None,
            "overlap_milliseconds": None,
            "attempt_count": 0,
            "failure_code": "segment_missing",
            "correction_started_at": None,
            "correction_completed_at": None,
            "text": None,
            "provider": None,
            "model": None,
        }
        for sequence in gaps
        if sequence not in present
    )
    return sorted(items, key=lambda item: int(item["sequence"]))


def text_state(segment: VoiceLiveRunSegmentDB) -> str:
    if not segment.result_ref or segment.text_revision <= 0:
        return "none"
    if segment.text_revision == 1:
        return "provisional"
    if segment.correction_status == "completed":
        return "final"
    return "final_uncorrected"


def gap_sequences(
    run: VoiceLiveRunDB,
    segments: tuple[VoiceLiveRunSegmentDB, ...],
) -> list[int]:
    by_sequence = {segment.sequence: segment for segment in segments}
    highest_expected = max(
        int(run.expected_last_sequence if run.expected_last_sequence is not None else -1),
        int(run.last_local_sequence if run.last_local_sequence is not None else -1),
        max(by_sequence, default=-1),
    )
    missing = {
        sequence
        for sequence in range(highest_expected + 1)
        if sequence not in by_sequence or by_sequence[sequence].status == "failed"
    }
    completed = {sequence for sequence, segment in by_sequence.items() if segment.status == "completed"}
    missing.update(int(item) for item in (run.reported_gap_sequences or []) if int(item) not in completed)
    return sorted(item for item in missing if 0 <= item <= highest_expected)


def acknowledged_through(segments: tuple[VoiceLiveRunSegmentDB, ...]) -> int:
    completed = {segment.sequence for segment in segments if segment.status == "completed"}
    sequence = 0
    while sequence in completed:
        sequence += 1
    return sequence - 1


def compose_transcript(
    values: list[tuple[VoiceLiveRunSegmentDB, str]],
) -> str:
    composed: list[str] = []
    previous_segment: VoiceLiveRunSegmentDB | None = None
    for segment, text in values:
        words = text.split()
        if not words:
            previous_segment = segment
            continue
        overlap = 0
        if composed and previous_segment is not None and segment.started_at_ms < previous_segment.ended_at_ms:
            maximum = min(32, len(composed), len(words))
            for width in range(maximum, 0, -1):
                left = [normalized_word(item) for item in composed[-width:]]
                right = [normalized_word(item) for item in words[:width]]
                if left == right and any(left):
                    overlap = width
                    break
        composed.extend(words[overlap:])
        previous_segment = segment
    return " ".join(composed).strip()


def public_run(run: VoiceLiveRunDB) -> dict[str, Any]:
    now = time.time()
    return {
        "id": run.id,
        "status": run.status,
        "source": run.source,
        "profile_id": run.profile_id,
        "configuration_session_id": run.configuration_session_id,
        "language": run.language,
        "parent_task_id": run.parent_task_id,
        "segment_duration_seconds": run.segment_duration_seconds,
        "max_duration_seconds": run.max_duration_seconds,
        "overlap_milliseconds": run.overlap_milliseconds,
        "last_local_sequence": run.last_local_sequence,
        "expected_last_sequence": run.expected_last_sequence,
        "last_heartbeat_at": run.last_heartbeat_at,
        "heartbeat_stale": run.status == "active" and now - run.last_heartbeat_at > 30,
        "capture_deadline_at": run.capture_deadline_at,
        "expires_at": run.expires_at,
        "final_result_ref": run.final_result_ref,
        "stop_reason": run.stop_reason,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "stopped_at": run.stopped_at,
        "version": run.version,
        "timeline_revision": run.timeline_revision,
    }


def normalized_word(value: str) -> str:
    return _WORD_NORMALIZER.sub("", value.casefold())


__all__ = [
    "acknowledged_through",
    "compose_transcript",
    "gap_sequences",
    "normalized_word",
    "public_run",
    "text_state",
    "timeline_items",
]
