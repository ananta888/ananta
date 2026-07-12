"""Deterministic incremental fusion for local streaming recognizers.

The Hub selects the execution policy.  This module only executes the already
selected local recognizers; it neither discovers work nor delegates to another
worker.  Candidate failures are isolated so one healthy recognizer can finish
the stream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Protocol

from .backends.base import CandidateError, TranscriptionCandidate, TranscriptionResult
from .fusion import DeterministicFusionService
from .fusion.alignment import detokenize, normalize_token, tokenize


class IncrementalModelRecognizer(Protocol):
    def accept(self, content: bytes) -> str | IncrementalHypothesis | None: ...

    def finish(self) -> TranscriptionResult: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class IncrementalHypothesis:
    """Optional richer update returned by a native streaming adapter.

    ``finalized_text`` is the transcript prefix covered by
    ``finalized_until_ms``.  Both values must be monotone.  Adapters that only
    return a string remain supported; their text is treated as revisable.
    """

    text: str
    finalized_text: str = ""
    finalized_until_ms: int | None = None

    def __post_init__(self) -> None:
        if self.finalized_until_ms is not None and self.finalized_until_ms < 0:
            raise ValueError("finalized time must be non-negative")
        if self.finalized_text and not _has_token_prefix(self.text, self.finalized_text):
            raise ValueError("finalized text must be a transcript prefix")
        if bool(self.finalized_text) != (self.finalized_until_ms is not None):
            raise ValueError("finalized text and time must be provided together")


@dataclass(frozen=True)
class StreamingModel:
    backend_id: str
    recognizer: IncrementalModelRecognizer

    def __post_init__(self) -> None:
        normalized = self.backend_id.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("streaming backend ID is invalid")


@dataclass(frozen=True)
class IncrementalFusionUpdate:
    """Serializable partial result consumed by ``StreamSession``."""

    text: str
    fusion_version: int
    text_revision: int
    stable_text: str
    stable_token_count: int
    finalized_until_ms: int | None
    candidates: tuple[dict[str, object], ...]
    disagreement: bool
    stability_conflict: bool
    finalized_conflict: bool
    trace_hash: str

    def as_payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "fusion_version": self.fusion_version,
            "text_revision": self.text_revision,
            "stable_text": self.stable_text,
            "stable_token_count": self.stable_token_count,
            "finalized_until_ms": self.finalized_until_ms,
            "candidates": [dict(item) for item in self.candidates],
            "disagreement": self.disagreement,
            "stability_conflict": self.stability_conflict,
            "finalized_conflict": self.finalized_conflict,
            "fusion_strategy": "incremental_deterministic_consensus",
            "trace_hash": self.trace_hash,
        }


class IncrementalFusionUnavailable(RuntimeError):
    """Raised after all configured local recognizers have failed."""


@dataclass
class _ModelState:
    backend_id: str
    recognizer: IncrementalModelRecognizer
    version: int = 0
    text: str = ""
    finalized_text: str = ""
    finalized_until_ms: int | None = None
    status: str = "active"
    reason_code: str | None = None

    @property
    def candidate_id(self) -> str:
        return f"stream-{self.backend_id}"


class IncrementalFusionRecognizer:
    """Execute and conservatively fuse multiple native streaming models.

    Fusion is deterministic: model state and candidate IDs are sorted by the
    caller-provided backend ID, and the existing deterministic consensus service
    selects only candidate-provenanced tokens.  A stable prefix needs two
    consecutive confirmations and is never retracted.  Explicitly finalized
    adapter ranges are additionally protected by their monotone timestamp.
    """

    def __init__(
        self,
        models: tuple[StreamingModel, ...],
        *,
        stability_confirmations: int = 2,
        fusion_service: DeterministicFusionService | None = None,
    ) -> None:
        if len(models) < 2:
            raise ValueError("incremental fusion requires at least two recognizers")
        ordered = sorted(models, key=lambda item: item.backend_id)
        if len({item.backend_id for item in ordered}) != len(ordered):
            raise ValueError("incremental fusion backend IDs must be unique")
        self._states = [_ModelState(backend_id=item.backend_id, recognizer=item.recognizer) for item in ordered]
        self._stability_confirmations = max(1, int(stability_confirmations))
        self._fusion = fusion_service or DeterministicFusionService()
        self._fusion_version = 0
        self._text_revision = 0
        self._text = ""
        self._stable_tokens: tuple[str, ...] = ()
        self._pending_stable_tokens: tuple[str, ...] = ()
        self._pending_confirmations = 0
        self._finalized_text = ""
        self._finalized_until_ms: int | None = None
        self._trace_hashes: list[str] = []
        self._closed = False

    def accept(self, content: bytes) -> IncrementalFusionUpdate | None:
        if self._closed:
            raise IncrementalFusionUnavailable("incremental fusion recognizer is closed")
        for state in self._states:
            if state.status != "active":
                continue
            try:
                raw = state.recognizer.accept(content)
                if raw is not None:
                    self._apply_hypothesis(state, _coerce_hypothesis(raw))
            except Exception:
                self._fail_model(state, "model_partial_failed")

        active = [state for state in self._states if state.status == "active"]
        if not active:
            raise IncrementalFusionUnavailable("all incremental recognizers failed")
        candidates = tuple(self._partial_candidate(state) for state in self._states)
        successful = tuple(item for item in candidates if item.status == "succeeded" and item.text.strip())
        if not successful:
            return None

        outcome = self._fusion.fuse(candidates)
        proposed = outcome.result.text
        finalized_conflict = self._advance_finalized_prefix(active)
        self._advance_stable_prefix(active)
        protected_text = self._protected_text()
        compatible = [state for state in active if not protected_text or _has_token_prefix(state.text, protected_text)]
        stability_conflict = bool(protected_text and len(compatible) != len(active))
        if protected_text and compatible:
            compatible_ids = {state.candidate_id for state in compatible}
            proposed = self._fusion.fuse(
                tuple(item for item in candidates if item.candidate_id in compatible_ids)
            ).result.text
        elif protected_text and not compatible:
            proposed = self._text or protected_text
            stability_conflict = True

        if protected_text and not _has_token_prefix(proposed, protected_text):
            proposed = self._text or protected_text
            stability_conflict = True
        if proposed != self._text:
            self._text = proposed
            self._text_revision += 1
        self._fusion_version += 1

        payload_candidates = tuple(self._candidate_payload(state) for state in self._states)
        disagreement = (
            len({tuple(normalize_token(token) for token in tokenize(state.text)) for state in active if state.text}) > 1
        )
        trace_hash = _trace_hash(
            fusion_version=self._fusion_version,
            text_revision=self._text_revision,
            text=self._text,
            stable_tokens=self._stable_tokens,
            finalized_until_ms=self._finalized_until_ms,
            candidates=payload_candidates,
            disagreement=disagreement,
            stability_conflict=stability_conflict,
            finalized_conflict=finalized_conflict,
        )
        self._trace_hashes.append(trace_hash)
        return IncrementalFusionUpdate(
            text=self._text,
            fusion_version=self._fusion_version,
            text_revision=self._text_revision,
            stable_text=detokenize(self._stable_tokens),
            stable_token_count=len(self._stable_tokens),
            finalized_until_ms=self._finalized_until_ms,
            candidates=payload_candidates,
            disagreement=disagreement,
            stability_conflict=stability_conflict,
            finalized_conflict=finalized_conflict,
            trace_hash=trace_hash,
        )

    def finish(self) -> TranscriptionResult:
        if self._closed:
            raise IncrementalFusionUnavailable("incremental fusion recognizer is closed")
        candidates: list[TranscriptionCandidate] = []
        for state in self._states:
            if state.status != "active":
                candidates.append(self._failed_candidate(state))
                continue
            try:
                result = state.recognizer.finish()
                if not result.text.strip():
                    raise ValueError("empty final transcript")
                global_protected = self._protected_text()
                protected_prefix = (
                    state.finalized_text
                    if len(tokenize(state.finalized_text)) >= len(tokenize(global_protected))
                    else global_protected
                )
                if protected_prefix and not _has_token_prefix(result.text, protected_prefix):
                    raise ValueError("final transcript replaced finalized text")
                candidates.append(
                    TranscriptionCandidate.from_result(
                        candidate_id=state.candidate_id,
                        backend=state.backend_id,
                        result=result,
                        lineage_id=state.candidate_id,
                    )
                )
                state.status = "final"
                state.version += 1
                state.text = result.text
            except Exception:
                self._fail_model(state, "model_final_failed")
                candidates.append(self._failed_candidate(state))
        outcome = self._fusion.fuse(tuple(candidates))
        if not outcome.result.text.strip():
            raise IncrementalFusionUnavailable("all incremental recognizers failed to finalize")
        return replace(
            outcome.result,
            pipeline="realtime_streaming_fusion",
            fusion_strategy="incremental_deterministic_consensus",
            decision_trace={
                **dict(outcome.result.decision_trace),
                "streaming_fusion": {
                    "fusion_versions": self._fusion_version,
                    "text_revisions": self._text_revision,
                    "stable_text": detokenize(self._stable_tokens),
                    "finalized_until_ms": self._finalized_until_ms,
                    "partial_trace_hashes": list(self._trace_hashes),
                    "candidate_versions": {state.backend_id: state.version for state in self._states},
                    "policy_owner": "hub",
                    "execution_location": "voice-runtime",
                },
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        for state in self._states:
            try:
                state.recognizer.close()
            except Exception:
                pass
            state.text = ""
            state.finalized_text = ""
            if state.status == "active":
                state.status = "cancelled"
                state.reason_code = "stream_cancelled"
                state.version += 1
        self._text = ""
        self._stable_tokens = ()
        self._pending_stable_tokens = ()
        self._finalized_text = ""
        self._trace_hashes.clear()
        self._closed = True

    def _apply_hypothesis(self, state: _ModelState, hypothesis: IncrementalHypothesis) -> None:
        changed = hypothesis.text != state.text
        if hypothesis.finalized_until_ms is not None:
            if state.finalized_until_ms is not None and hypothesis.finalized_until_ms < state.finalized_until_ms:
                raise ValueError("model finalized timestamp regressed")
            if state.finalized_text and not _has_token_prefix(hypothesis.finalized_text, state.finalized_text):
                raise ValueError("model finalized text regressed")
            changed = changed or (
                hypothesis.finalized_text != state.finalized_text
                or hypothesis.finalized_until_ms != state.finalized_until_ms
            )
            state.finalized_text = hypothesis.finalized_text
            state.finalized_until_ms = hypothesis.finalized_until_ms
        state.text = hypothesis.text
        if changed:
            state.version += 1

    def _advance_stable_prefix(self, active: list[_ModelState]) -> None:
        texts = [tokenize(state.text) for state in active if state.text]
        if len(texts) < 2:
            self._reset_pending_stability()
            return
        common = _common_token_prefix(texts)
        if self._stable_tokens and not _normalized_prefix(common, self._stable_tokens):
            self._reset_pending_stability()
            return
        # Keep the last token revisable unless an adapter explicitly finalized it.
        promotable = common[:-1] if len(common) > len(self._stable_tokens) else common
        if len(promotable) <= len(self._stable_tokens):
            self._reset_pending_stability()
            return
        if _normalized_tokens(promotable) == _normalized_tokens(self._pending_stable_tokens):
            self._pending_confirmations += 1
        else:
            self._pending_stable_tokens = promotable
            self._pending_confirmations = 1
        if self._pending_confirmations >= self._stability_confirmations:
            self._stable_tokens = promotable

    def _advance_finalized_prefix(self, active: list[_ModelState]) -> bool:
        if len(active) < 2:
            return False
        finalized = [state for state in active if state.finalized_text and state.finalized_until_ms is not None]
        if len(finalized) != len(active):
            return False
        finalized_tokens = [tokenize(state.finalized_text) for state in finalized]
        normalized = {_normalized_tokens(tokens) for tokens in finalized_tokens}
        if len(normalized) != 1:
            return False
        proposed_tokens = finalized_tokens[0]
        proposed = detokenize(proposed_tokens)
        if self._finalized_text and not _has_token_prefix(proposed, self._finalized_text):
            return True
        proposed_until_ms = min(state.finalized_until_ms or 0 for state in finalized)
        if self._finalized_until_ms is not None and proposed_until_ms < self._finalized_until_ms:
            return True
        if len(proposed_tokens) >= len(tokenize(self._finalized_text)):
            self._finalized_text = proposed
            self._finalized_until_ms = proposed_until_ms
            if len(proposed_tokens) > len(self._stable_tokens):
                self._stable_tokens = proposed_tokens
                self._reset_pending_stability()
        return False

    def _reset_pending_stability(self) -> None:
        self._pending_stable_tokens = ()
        self._pending_confirmations = 0

    def _protected_text(self) -> str:
        stable_text = detokenize(self._stable_tokens)
        if len(tokenize(self._finalized_text)) > len(self._stable_tokens):
            return self._finalized_text
        return stable_text

    @staticmethod
    def _partial_candidate(state: _ModelState) -> TranscriptionCandidate:
        if state.status != "active":
            return IncrementalFusionRecognizer._failed_candidate(state)
        return TranscriptionCandidate(
            candidate_id=state.candidate_id,
            backend=state.backend_id,
            text=state.text,
            status="succeeded",
            lineage_id=state.candidate_id,
            provenance={
                "streaming": True,
                "candidate_version": state.version,
                "execution_location": "voice-runtime",
            },
        )

    @staticmethod
    def _failed_candidate(state: _ModelState) -> TranscriptionCandidate:
        return TranscriptionCandidate(
            candidate_id=state.candidate_id,
            backend=state.backend_id,
            text="",
            status="failed",
            error=CandidateError(
                code=state.reason_code or "model_failed",
                message="streaming model failed",
                retriable=False,
            ),
            lineage_id=state.candidate_id,
            provenance={
                "streaming": True,
                "candidate_version": state.version,
                "execution_location": "voice-runtime",
            },
        )

    @staticmethod
    def _candidate_payload(state: _ModelState) -> dict[str, object]:
        return {
            "candidate_id": state.candidate_id,
            "backend": state.backend_id,
            "candidate_version": state.version,
            "text": state.text if state.status == "active" else "",
            "status": state.status,
            "reason_code": state.reason_code,
            "finalized_text": state.finalized_text,
            "finalized_until_ms": state.finalized_until_ms,
        }

    @staticmethod
    def _fail_model(state: _ModelState, reason_code: str) -> None:
        state.status = "failed"
        state.reason_code = reason_code
        state.version += 1
        state.text = ""
        try:
            state.recognizer.close()
        except Exception:
            pass


def _coerce_hypothesis(value: str | IncrementalHypothesis) -> IncrementalHypothesis:
    return value if isinstance(value, IncrementalHypothesis) else IncrementalHypothesis(text=str(value))


def _common_token_prefix(values: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not values:
        return ()
    prefix = values[0]
    for tokens in values[1:]:
        length = 0
        for left, right in zip(prefix, tokens, strict=False):
            if normalize_token(left) != normalize_token(right):
                break
            length += 1
        prefix = prefix[:length]
    return prefix


def _normalized_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_token(item) for item in tokens)


def _normalized_prefix(value: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return _normalized_tokens(value[: len(prefix)]) == _normalized_tokens(prefix)


def _has_token_prefix(value: str, prefix: str) -> bool:
    return _normalized_prefix(tokenize(value), tokenize(prefix))


def _trace_hash(
    *,
    fusion_version: int,
    text_revision: int,
    text: str,
    stable_tokens: tuple[str, ...],
    finalized_until_ms: int | None,
    candidates: tuple[dict[str, object], ...],
    disagreement: bool,
    stability_conflict: bool,
    finalized_conflict: bool,
) -> str:
    payload = {
        "fusion_version": fusion_version,
        "text_revision": text_revision,
        "text": text,
        "stable_text": detokenize(stable_tokens),
        "finalized_until_ms": finalized_until_ms,
        "candidates": candidates,
        "disagreement": disagreement,
        "stability_conflict": stability_conflict,
        "finalized_conflict": finalized_conflict,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
