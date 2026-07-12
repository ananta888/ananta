from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from voice_runtime.backends.base import TranscriptionCandidate

_TOKEN_RE = re.compile(r"\w+(?:[-'’]\w+)*|[^\w\s]", re.UNICODE)


def normalize_token(value: str) -> str:
    """Return the locale-independent token form used by fusion."""

    return unicodedata.normalize("NFKC", value).casefold()


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(unicodedata.normalize("NFC", str(value))))


def detokenize(tokens: tuple[str, ...] | list[str]) -> str:
    text = ""
    no_space_before = frozenset({".", ",", ":", ";", "!", "?", ")", "]", "}", "%"})
    no_space_after = frozenset({"(", "[", "{"})
    for token in tokens:
        if not text or token in no_space_before or text[-1] in no_space_after:
            text += token
        else:
            text += f" {token}"
    return text


@dataclass(frozen=True)
class TokenSource:
    """Backward-compatible projection of one candidate onto anchor positions."""

    anchor_index: int
    candidate_index: int | None
    token: str | None


def align_tokens_to_anchor(
    anchor: tuple[str, ...], candidate: tuple[str, ...]
) -> tuple[TokenSource, ...]:
    """Unicode-normalized deterministic fallback for candidates without times.

    Unequal replacements and insertions stay outside this per-token projection;
    the region-level alignment records them without inventing a token mapping.
    """

    matcher = SequenceMatcher(
        a=tuple(normalize_token(item) for item in anchor),
        b=tuple(normalize_token(item) for item in candidate),
        autojunk=False,
    )
    mapped: dict[int, TokenSource] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for offset in range(i2 - i1):
                mapped[i1 + offset] = TokenSource(i1 + offset, j1 + offset, candidate[j1 + offset])
        elif tag == "delete":
            for anchor_index in range(i1, i2):
                mapped[anchor_index] = TokenSource(anchor_index, None, None)
    return tuple(mapped.get(index, TokenSource(index, None, None)) for index in range(len(anchor)))


TimeSource = Literal["word", "segment_bounds", "unavailable"]
AlignmentMethod = Literal["time_v1", "unicode_text_v1"]
AlignmentOperation = Literal["equal", "replace", "insert", "delete"]


@dataclass(frozen=True)
class CandidateToken:
    index: int
    text: str
    start_ms: int | None
    end_ms: int | None
    time_source: TimeSource

    @property
    def normalized(self) -> str:
        return normalize_token(self.text)

    @property
    def has_time(self) -> bool:
        return self.start_ms is not None and self.end_ms is not None


@dataclass(frozen=True)
class AlignmentSpan:
    operation: AlignmentOperation
    reference_start_index: int
    reference_end_index: int
    candidate_start_index: int
    candidate_end_index: int
    reference_text: str
    candidate_text: str
    start_ms: int | None
    end_ms: int | None
    method: AlignmentMethod


@dataclass(frozen=True)
class CandidateAlignment:
    reference_candidate_id: str
    candidate_id: str
    method: AlignmentMethod
    reference_tokens: tuple[CandidateToken, ...]
    candidate_tokens: tuple[CandidateToken, ...]
    spans: tuple[AlignmentSpan, ...]

    def project_to_reference(self) -> tuple[TokenSource, ...]:
        mapped: dict[int, TokenSource] = {}
        for span in self.spans:
            reference_count = span.reference_end_index - span.reference_start_index
            candidate_count = span.candidate_end_index - span.candidate_start_index
            if span.operation in {"equal", "replace"} and reference_count == candidate_count:
                for offset in range(reference_count):
                    candidate_index = span.candidate_start_index + offset
                    mapped[span.reference_start_index + offset] = TokenSource(
                        anchor_index=span.reference_start_index + offset,
                        candidate_index=candidate_index,
                        token=self.candidate_tokens[candidate_index].text,
                    )
            elif span.operation == "delete":
                for reference_index in range(span.reference_start_index, span.reference_end_index):
                    mapped[reference_index] = TokenSource(reference_index, None, None)
        return tuple(
            mapped.get(index, TokenSource(index, None, None))
            for index in range(len(self.reference_tokens))
        )


def candidate_tokens(candidate: TranscriptionCandidate) -> tuple[CandidateToken, ...]:
    """Tokenize text and attach only timestamps present in source artifacts.

    Segment bounds are copied as coarse source evidence; they are never split or
    interpolated into fabricated word times.
    """

    tokens = tokenize(candidate.text)
    evidence: dict[int, tuple[int, int, TimeSource]] = {}
    words = candidate.words or tuple(word for segment in candidate.segments for word in segment.words)
    if words:
        word_tokens: list[str] = []
        word_evidence: list[tuple[int, int, TimeSource]] = []
        for word in words:
            pieces = tokenize(word.text)
            word_tokens.extend(pieces)
            word_evidence.extend((word.start_ms, word.end_ms, "word") for _ in pieces)
        evidence.update(_project_evidence(tokens, tuple(word_tokens), tuple(word_evidence)))

    for segment in candidate.segments:
        pieces = tokenize(segment.text)
        segment_evidence: tuple[tuple[int, int, TimeSource], ...] = tuple(
            (segment.start_ms, segment.end_ms, "segment_bounds") for _ in pieces
        )
        for index, item in _project_evidence(tokens, pieces, segment_evidence).items():
            evidence.setdefault(index, item)

    return tuple(
        CandidateToken(
            index=index,
            text=token,
            start_ms=evidence[index][0] if index in evidence else None,
            end_ms=evidence[index][1] if index in evidence else None,
            time_source=evidence[index][2] if index in evidence else "unavailable",
        )
        for index, token in enumerate(tokens)
    )


def align_candidates(
    reference: TranscriptionCandidate,
    candidate: TranscriptionCandidate,
) -> CandidateAlignment:
    reference_tokens = candidate_tokens(reference)
    compared_tokens = candidate_tokens(candidate)
    use_time = bool(reference_tokens and compared_tokens) and any(
        item.has_time for item in reference_tokens
    ) and any(
        item.has_time for item in compared_tokens
    )
    method: AlignmentMethod = "time_v1" if use_time else "unicode_text_v1"
    raw_operations = (
        _time_operations(reference_tokens, compared_tokens)
        if use_time
        else _text_operations(reference_tokens, compared_tokens)
    )
    spans = tuple(
        _span_from_operation(operation, reference_tokens, compared_tokens, method)
        for operation in raw_operations
    )
    return CandidateAlignment(
        reference_candidate_id=reference.candidate_id,
        candidate_id=candidate.candidate_id,
        method=method,
        reference_tokens=reference_tokens,
        candidate_tokens=compared_tokens,
        spans=spans,
    )


@dataclass(frozen=True)
class AlignedDifference:
    start_index: int
    end_index: int
    candidate_start_index: int
    candidate_end_index: int
    reference_text: str
    alternative_text: str
    candidate_id: str
    start_ms: int | None
    end_ms: int | None
    alignment_method: AlignmentMethod
    operation: AlignmentOperation


def differences(
    reference: TranscriptionCandidate,
    candidate: TranscriptionCandidate,
) -> tuple[AlignedDifference, ...]:
    alignment = align_candidates(reference, candidate)
    return tuple(
        AlignedDifference(
            start_index=span.reference_start_index,
            end_index=span.reference_end_index,
            candidate_start_index=span.candidate_start_index,
            candidate_end_index=span.candidate_end_index,
            reference_text=span.reference_text,
            alternative_text=span.candidate_text,
            candidate_id=candidate.candidate_id,
            start_ms=span.start_ms,
            end_ms=span.end_ms,
            alignment_method=span.method,
            operation=span.operation,
        )
        for span in alignment.spans
        if span.operation != "equal"
    )


def _project_evidence(
    target_tokens: tuple[str, ...],
    evidence_tokens: tuple[str, ...],
    evidence: tuple[tuple[int, int, TimeSource], ...],
) -> dict[int, tuple[int, int, TimeSource]]:
    if len(evidence_tokens) != len(evidence):
        return {}
    matcher = SequenceMatcher(
        a=tuple(normalize_token(item) for item in target_tokens),
        b=tuple(normalize_token(item) for item in evidence_tokens),
        autojunk=False,
    )
    projected: dict[int, tuple[int, int, TimeSource]] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            projected[i1 + offset] = evidence[j1 + offset]
    return projected


def _text_operations(
    reference: tuple[CandidateToken, ...],
    candidate: tuple[CandidateToken, ...],
) -> tuple[tuple[AlignmentOperation, int, int, int, int], ...]:
    matcher = SequenceMatcher(
        a=tuple(item.normalized for item in reference),
        b=tuple(item.normalized for item in candidate),
        autojunk=False,
    )
    return tuple(
        (tag, i1, i2, j1, j2)  # type: ignore[arg-type]
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def _time_operations(
    reference: tuple[CandidateToken, ...],
    candidate: tuple[CandidateToken, ...],
) -> tuple[tuple[AlignmentOperation, int, int, int, int], ...]:
    """Needleman-Wunsch alignment with integer, deterministic time costs."""

    rows = len(reference) + 1
    columns = len(candidate) + 1
    costs = [[0] * columns for _ in range(rows)]
    moves: list[list[AlignmentOperation | None]] = [[None] * columns for _ in range(rows)]
    gap_cost = 1_000
    for index in range(1, rows):
        costs[index][0] = index * gap_cost
        moves[index][0] = "delete"
    for index in range(1, columns):
        costs[0][index] = index * gap_cost
        moves[0][index] = "insert"

    for i in range(1, rows):
        for j in range(1, columns):
            diagonal = costs[i - 1][j - 1] + _pair_cost(reference[i - 1], candidate[j - 1])
            deleted = costs[i - 1][j] + gap_cost
            inserted = costs[i][j - 1] + gap_cost
            # The tuple order is the documented stable tie-breaker: source-pair,
            # reference deletion, candidate insertion.
            _, move_rank, cost = min(
                (diagonal, 0, diagonal),
                (deleted, 1, deleted),
                (inserted, 2, inserted),
            )
            costs[i][j] = cost
            if move_rank == 0:
                moves[i][j] = (
                    "equal"
                    if reference[i - 1].normalized == candidate[j - 1].normalized
                    else "replace"
                )
            else:
                moves[i][j] = "delete" if move_rank == 1 else "insert"

    atomic: list[tuple[AlignmentOperation, int, int, int, int]] = []
    i, j = len(reference), len(candidate)
    while i or j:
        backtrace_move = moves[i][j]
        if backtrace_move in {"equal", "replace"}:
            atomic.append((backtrace_move, i - 1, i, j - 1, j))
            i -= 1
            j -= 1
        elif backtrace_move == "delete":
            atomic.append((backtrace_move, i - 1, i, j, j))
            i -= 1
        elif backtrace_move == "insert":
            atomic.append((backtrace_move, i, i, j - 1, j))
            j -= 1
        else:  # pragma: no cover - defensive invariant
            raise RuntimeError("time alignment backtrace is incomplete")
    atomic.reverse()
    return _coalesce_operations(tuple(atomic))


def _pair_cost(reference: CandidateToken, candidate: CandidateToken) -> int:
    if not reference.has_time or not candidate.has_time:
        # A partially timed transcript stays in the time alignment while this
        # pair falls back to Unicode text evidence. Unequal forms remain more
        # expensive than a timed overlapping replacement.
        return 0 if reference.normalized == candidate.normalized else 1_750
    assert reference.start_ms is not None and reference.end_ms is not None
    assert candidate.start_ms is not None and candidate.end_ms is not None
    overlap = max(
        0,
        min(reference.end_ms, candidate.end_ms)
        - max(reference.start_ms, candidate.start_ms),
    )
    union = max(
        1,
        max(reference.end_ms, candidate.end_ms)
        - min(reference.start_ms, candidate.start_ms),
    )
    overlap_penalty = 1_000 - (overlap * 1_000 // union)
    text_penalty = 0 if reference.normalized == candidate.normalized else 750
    return overlap_penalty + text_penalty


def _coalesce_operations(
    operations: tuple[tuple[AlignmentOperation, int, int, int, int], ...],
) -> tuple[tuple[AlignmentOperation, int, int, int, int], ...]:
    result: list[tuple[AlignmentOperation, int, int, int, int]] = []
    for operation in operations:
        if (
            result
            and result[-1][0] == operation[0]
            and result[-1][2] == operation[1]
            and result[-1][4] == operation[3]
        ):
            previous = result[-1]
            result[-1] = (previous[0], previous[1], operation[2], previous[3], operation[4])
        else:
            result.append(operation)
    return tuple(result)


def _span_from_operation(
    operation: tuple[AlignmentOperation, int, int, int, int],
    reference: tuple[CandidateToken, ...],
    candidate: tuple[CandidateToken, ...],
    method: AlignmentMethod,
) -> AlignmentSpan:
    tag, i1, i2, j1, j2 = operation
    related = (*reference[i1:i2], *candidate[j1:j2])
    starts = [int(item.start_ms) for item in related if item.start_ms is not None]
    ends = [int(item.end_ms) for item in related if item.end_ms is not None]
    return AlignmentSpan(
        operation=tag,
        reference_start_index=i1,
        reference_end_index=i2,
        candidate_start_index=j1,
        candidate_end_index=j2,
        reference_text=detokenize([item.text for item in reference[i1:i2]]),
        candidate_text=detokenize([item.text for item in candidate[j1:j2]]),
        start_ms=min(starts) if starts else None,
        end_ms=max(ends) if ends else None,
        method=method,
    )
