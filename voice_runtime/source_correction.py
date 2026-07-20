"""Bounded source-audio correction composed with the canonical fusion alignment.

The Hub decides when a finalized segment may be corrected and delegates one
request.  This module performs that request locally; it owns neither a task
queue nor a retry loop.  Idempotency prevents duplicate Hub delivery from
running alignment or retention cleanup twice.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from voice_runtime.backends.base import TranscriptionCandidate, TranscriptionResult
from voice_runtime.fusion.alignment import align_candidates, tokenize

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_CORRECTION_TOKENS = 4_096
MAX_CORRECTION_TEXT_BYTES = 65_536
MAX_CACHED_ATTEMPTS = 2_048

CorrectionAuthority = Literal["corrected", "final", "correction_failed", "missing_source"]


@dataclass(frozen=True, slots=True)
class SourceCorrectionRequest:
    session_id: str
    epoch: int
    turn_id: str
    provisional_revision: int
    consent_version: int
    source_digest: str
    source_expires_at_ms: int
    deadline_at_ms: int
    requested_at_ms: int
    consent_granted: bool = True

    @property
    def attempt_key(self) -> tuple[str, int, str, int, str]:
        return (
            self.session_id,
            self.epoch,
            self.turn_id,
            self.provisional_revision,
            self.source_digest,
        )


@dataclass(frozen=True, slots=True)
class SourceCorrectionOperation:
    kind: Literal["equal", "insert", "delete", "replace"]
    reference_text: str
    candidate_text: str
    candidate_id: str
    start_ms: int | None
    end_ms: int | None
    confidence: float | None
    alignment_method: Literal["time_v1", "unicode_text_v1"]


@dataclass(frozen=True, slots=True)
class SourceCorrectionResult:
    session_id: str
    epoch: int
    turn_id: str
    revision: int
    supersedes_revision: int
    text: str
    authority: CorrectionAuthority
    reason_code: str
    source_digest: str
    operations: tuple[SourceCorrectionOperation, ...] = ()
    correction_attempted: bool = False

    def apply_to(self, original: TranscriptionResult) -> TranscriptionResult:
        return replace(
            original,
            text=self.text,
            turn_id=self.turn_id,
            revision=self.revision,
            supersedes_revision=self.supersedes_revision,
            authority=self.authority,
            source_digest=self.source_digest,
            correction_state=self.reason_code,
            decision_trace={
                **dict(original.decision_trace),
                "source_correction": {
                    "reason_code": self.reason_code,
                    "correction_attempted": self.correction_attempted,
                    "operation_count": len(self.operations),
                    "candidate_ids": sorted({item.candidate_id for item in self.operations}),
                },
            },
        )


class SourceRetentionPort(Protocol):
    """Deletion port owned by the caller's short-lived source buffer."""

    def release(self, *, session_id: str, source_digest: str, reason_code: str) -> None: ...


class SourceCorrectionPort(Protocol):
    def correct(
        self,
        *,
        request: SourceCorrectionRequest,
        provisional: TranscriptionCandidate,
        source: TranscriptionCandidate | None,
    ) -> SourceCorrectionResult: ...


class NullSourceRetention:
    def release(self, *, session_id: str, source_digest: str, reason_code: str) -> None:
        del session_id, source_digest, reason_code


class SourceCorrectionService:
    """Exactly-once, bounded correction without autonomous orchestration."""

    def __init__(
        self,
        retention: SourceRetentionPort | None = None,
        *,
        max_cached_attempts: int = MAX_CACHED_ATTEMPTS,
    ) -> None:
        if not 1 <= max_cached_attempts <= MAX_CACHED_ATTEMPTS:
            raise ValueError("source_correction_cache_budget_invalid")
        self._retention = retention or NullSourceRetention()
        self._max_cached_attempts = max_cached_attempts
        self._attempts: OrderedDict[tuple[str, int, str, int, str], SourceCorrectionResult] = OrderedDict()

    def correct(
        self,
        *,
        request: SourceCorrectionRequest,
        provisional: TranscriptionCandidate,
        source: TranscriptionCandidate | None,
    ) -> SourceCorrectionResult:
        self._validate_request(request)
        cached = self._attempts.get(request.attempt_key)
        if cached is not None:
            return cached

        result = self._evaluate(request=request, provisional=provisional, source=source)
        try:
            self._retention.release(
                session_id=request.session_id,
                source_digest=request.source_digest,
                reason_code=result.reason_code,
            )
        except Exception:
            result = self._failure(
                request,
                provisional,
                authority="correction_failed",
                reason_code="source_release_failed",
                attempted=result.correction_attempted,
            )
        self._attempts[request.attempt_key] = result
        self._attempts.move_to_end(request.attempt_key)
        while len(self._attempts) > self._max_cached_attempts:
            self._attempts.popitem(last=False)
        return result

    def snapshot(self) -> dict[str, int]:
        return {"cached_attempts": len(self._attempts), "timers": 0, "inflight": 0}

    def _evaluate(
        self,
        *,
        request: SourceCorrectionRequest,
        provisional: TranscriptionCandidate,
        source: TranscriptionCandidate | None,
    ) -> SourceCorrectionResult:
        if not request.consent_granted:
            return self._failure(request, provisional, "correction_failed", "consent_revoked")
        if request.requested_at_ms >= request.source_expires_at_ms or source is None:
            return self._failure(request, provisional, "missing_source", "source_missing_or_expired")
        if request.requested_at_ms >= request.deadline_at_ms:
            return self._failure(request, provisional, "correction_failed", "correction_deadline_elapsed")
        if source.status != "succeeded":
            return self._failure(request, provisional, "correction_failed", "source_transcription_failed", True)
        if source.source_audio_digest != request.source_digest:
            return self._failure(request, provisional, "correction_failed", "source_digest_mismatch")
        if provisional.status != "succeeded":
            return self._failure(request, provisional, "correction_failed", "provisional_candidate_invalid")
        if not self._within_budget(provisional) or not self._within_budget(source):
            return self._failure(request, provisional, "correction_failed", "correction_budget_exceeded")

        try:
            alignment = align_candidates(provisional, source)
        except Exception:
            return self._failure(request, provisional, "correction_failed", "source_alignment_failed", True)
        if request.requested_at_ms >= request.deadline_at_ms:
            return self._failure(request, provisional, "correction_failed", "correction_deadline_elapsed", True)

        operations = tuple(
            SourceCorrectionOperation(
                kind=span.operation,
                reference_text=span.reference_text,
                candidate_text=span.candidate_text,
                candidate_id=source.candidate_id,
                start_ms=span.start_ms,
                end_ms=span.end_ms,
                confidence=source.confidence,
                alignment_method=span.method,
            )
            for span in alignment.spans
        )
        changed = any(item.kind != "equal" for item in operations)
        return SourceCorrectionResult(
            session_id=request.session_id,
            epoch=request.epoch,
            turn_id=request.turn_id,
            revision=request.provisional_revision + 1,
            supersedes_revision=request.provisional_revision,
            text=source.text if changed else provisional.text,
            authority="corrected" if changed else "final",
            reason_code="corrected" if changed else "source_agrees",
            source_digest=request.source_digest,
            operations=operations,
            correction_attempted=True,
        )

    @staticmethod
    def _within_budget(candidate: TranscriptionCandidate) -> bool:
        return (
            len(candidate.text.encode("utf-8")) <= MAX_CORRECTION_TEXT_BYTES
            and len(tokenize(candidate.text)) <= MAX_CORRECTION_TOKENS
        )

    @staticmethod
    def _failure(
        request: SourceCorrectionRequest,
        provisional: TranscriptionCandidate,
        authority: Literal["correction_failed", "missing_source"],
        reason_code: str,
        attempted: bool = False,
    ) -> SourceCorrectionResult:
        return SourceCorrectionResult(
            session_id=request.session_id,
            epoch=request.epoch,
            turn_id=request.turn_id,
            revision=request.provisional_revision + 1,
            supersedes_revision=request.provisional_revision,
            text=provisional.text,
            authority=authority,
            reason_code=reason_code,
            source_digest=request.source_digest,
            correction_attempted=attempted,
        )

    @staticmethod
    def _validate_request(request: SourceCorrectionRequest) -> None:
        if not _ID.fullmatch(request.session_id) or not _ID.fullmatch(request.turn_id):
            raise ValueError("source_correction_identity_invalid")
        if not _DIGEST.fullmatch(request.source_digest):
            raise ValueError("source_correction_digest_invalid")
        integer_fields = (
            request.epoch,
            request.provisional_revision,
            request.consent_version,
            request.source_expires_at_ms,
            request.deadline_at_ms,
            request.requested_at_ms,
        )
        if any(type(item) is not int or item < 1 for item in integer_fields):
            raise ValueError("source_correction_context_invalid")


__all__ = [
    "SourceCorrectionOperation",
    "SourceCorrectionPort",
    "SourceCorrectionRequest",
    "SourceCorrectionResult",
    "SourceCorrectionService",
    "SourceRetentionPort",
]
