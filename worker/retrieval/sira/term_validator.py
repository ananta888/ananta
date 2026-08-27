from __future__ import annotations

import re
from collections.abc import Sequence

from worker.retrieval.sira.config import SiraConfig
from worker.retrieval.sira.contracts import (
    CorpusBinding,
    CorpusTermStatisticsPort,
    GeneratedTerm,
    TermDecision,
)

_RANDOM_IDENTIFIER = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d_-]{20,}$")


class CorpusTermValidator:
    def __init__(self, *, statistics: CorpusTermStatisticsPort, config: SiraConfig):
        self._statistics = statistics
        self._config = config

    def validate(
        self,
        terms: Sequence[GeneratedTerm],
        *,
        binding: CorpusBinding,
        original_terms: Sequence[str] = (),
    ) -> tuple[TermDecision, ...]:
        proposed = list(terms)[: self._config.max_generated_terms]
        stats = self._statistics.lookup([term.value for term in proposed], binding=binding)
        original = {str(value).casefold() for value in original_terms}
        accepted_values: set[str] = set()
        decisions: list[TermDecision] = []
        for term in proposed:
            key = term.value.casefold()
            stat = stats.get(key) or stats.get(term.value)
            reason = "accepted"
            accepted = True
            if term.confidence < self._config.minimum_term_confidence:
                accepted, reason = False, "confidence_below_threshold"
            elif key in original:
                accepted, reason = False, "redundant_with_original"
            elif key in accepted_values:
                accepted, reason = False, "duplicate_term"
            elif stat is None or stat.document_frequency <= 0:
                accepted, reason = False, "term_absent_from_scope"
            elif stat.document_frequency_ratio > self._config.maximum_document_frequency_ratio:
                accepted, reason = False, "document_frequency_too_high"
            elif _RANDOM_IDENTIFIER.fullmatch(term.value) and stat.document_frequency <= 1:
                accepted, reason = False, "rare_identifier_untrusted"
            elif not stat.fields:
                accepted, reason = False, "term_field_absent"
            if accepted:
                accepted_values.add(key)
            decisions.append(TermDecision(term=term, accepted=accepted, reason_code=reason, stat=stat))
        return tuple(decisions)
