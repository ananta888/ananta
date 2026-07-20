from __future__ import annotations

from dataclasses import replace

from voice_runtime.backends.base import TranscriptionCandidate, TranscriptionWord
from voice_runtime.source_correction import SourceCorrectionRequest, SourceCorrectionService


class _Retention:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def release(self, *, session_id: str, source_digest: str, reason_code: str) -> None:
        self.calls.append((session_id, source_digest, reason_code))


def _request(**changes: object) -> SourceCorrectionRequest:
    value = SourceCorrectionRequest(
        session_id="session-a",
        epoch=2,
        turn_id="turn-a",
        provisional_revision=3,
        consent_version=4,
        source_digest="a" * 64,
        source_expires_at_ms=20_000,
        deadline_at_ms=15_000,
        requested_at_ms=10_000,
    )
    return replace(value, **changes)


def _candidate(candidate_id: str, text: str, *, source_digest: str | None = None) -> TranscriptionCandidate:
    words = tuple(
        TranscriptionWord(index * 100, (index + 1) * 100, token, confidence=0.8, candidate_id=candidate_id)
        for index, token in enumerate(text.split())
    )
    return TranscriptionCandidate(
        candidate_id=candidate_id,
        backend="fixture",
        text=text,
        words=words,
        confidence=0.8,
        source_audio_digest=source_digest,
    )


def test_source_correction_uses_canonical_alignment_and_explicit_provenance() -> None:
    retention = _Retention()
    service = SourceCorrectionService(retention)

    result = service.correct(
        request=_request(),
        provisional=_candidate("live", "Wir testen alte Worte"),
        source=_candidate("source", "Wir testen neue klare Worte", source_digest="a" * 64),
    )

    assert result.authority == "corrected"
    assert result.text == "Wir testen neue klare Worte"
    assert result.supersedes_revision == 3 and result.revision == 4
    assert {item.kind for item in result.operations} >= {"equal", "replace"}
    assert all(item.candidate_id == "source" for item in result.operations)
    assert all(item.alignment_method == "time_v1" for item in result.operations)
    assert retention.calls == [("session-a", "a" * 64, "corrected")]


def test_duplicate_attempt_returns_bitstable_result_and_releases_source_once() -> None:
    retention = _Retention()
    service = SourceCorrectionService(retention)
    request = _request()
    provisional = _candidate("live", "eins zwei")
    source = _candidate("source", "eins drei", source_digest="a" * 64)

    first = service.correct(request=request, provisional=provisional, source=source)
    second = service.correct(request=request, provisional=provisional, source=source)

    assert first == second
    assert len(retention.calls) == 1
    assert service.snapshot() == {"cached_attempts": 1, "timers": 0, "inflight": 0}


def test_missing_expired_or_revoked_source_keeps_final_transcript_visible() -> None:
    retention = _Retention()
    service = SourceCorrectionService(retention)
    provisional = _candidate("live", "sichtbarer finaler Text")

    missing = service.correct(request=_request(), provisional=provisional, source=None)
    expired = service.correct(
        request=_request(turn_id="turn-b", requested_at_ms=20_000),
        provisional=provisional,
        source=_candidate("source", "anderer Text", source_digest="a" * 64),
    )
    revoked = service.correct(
        request=_request(turn_id="turn-c", consent_granted=False),
        provisional=provisional,
        source=_candidate("source", "anderer Text", source_digest="a" * 64),
    )

    assert (missing.authority, missing.reason_code, missing.text) == (
        "missing_source",
        "source_missing_or_expired",
        provisional.text,
    )
    assert (expired.authority, expired.reason_code, expired.text) == (
        "missing_source",
        "source_missing_or_expired",
        provisional.text,
    )
    assert (revoked.authority, revoked.reason_code, revoked.text) == (
        "correction_failed",
        "consent_revoked",
        provisional.text,
    )
    assert len(retention.calls) == 3


def test_digest_mismatch_and_budget_failure_never_apply_unprovenanced_words() -> None:
    retention = _Retention()
    service = SourceCorrectionService(retention)
    provisional = _candidate("live", "original")

    mismatch = service.correct(
        request=_request(),
        provisional=provisional,
        source=_candidate("source", "invented", source_digest="b" * 64),
    )
    oversized = service.correct(
        request=_request(turn_id="turn-large"),
        provisional=provisional,
        source=_candidate("source", "x " * 4_097, source_digest="a" * 64),
    )

    assert mismatch.text == provisional.text and mismatch.reason_code == "source_digest_mismatch"
    assert oversized.text == provisional.text and oversized.reason_code == "correction_budget_exceeded"
    assert not mismatch.operations and not oversized.operations
