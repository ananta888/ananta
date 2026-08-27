from __future__ import annotations

import math
import re
from collections.abc import Sequence

from worker.retrieval.sira.contracts import CompiledQuery, CorpusBinding, TermDecision, WeightedTerm

_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ_][A-Za-zÀ-ÖØ-öø-ÿ0-9_]{1,79}")


def tokenize_original_query(query: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _TOKEN.findall(str(query or "")):
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _fts_quote(term: str) -> str:
    tokens = _TOKEN.findall(term)
    if not tokens:
        return ""
    return '"' + " ".join(tokens).replace('"', '""') + '"'


class WeightedQueryCompiler:
    MAX_WEIGHT = 8.0
    MIN_WEIGHT = 0.1
    MAX_TERMS = 64

    def compile(
        self,
        *,
        original_query: str,
        decisions: Sequence[TermDecision],
        binding: CorpusBinding,
    ) -> CompiledQuery:
        original_terms = tokenize_original_query(original_query)
        weighted: list[WeightedTerm] = [
            WeightedTerm(value=value, weight=4.0, origin="original", reason="original_query_preserved")
            for value in original_terms
        ]
        seen = {value.casefold() for value in original_terms}
        for decision in decisions:
            if not decision.accepted or decision.stat is None:
                continue
            key = decision.term.value.casefold()
            if key in seen or len(weighted) >= self.MAX_TERMS:
                continue
            seen.add(key)
            stat = decision.stat
            idf = math.log(1.0 + (max(1, stat.document_count) / max(1, stat.document_frequency)))
            confidence = max(0.0, min(1.0, float(decision.term.confidence)))
            weight = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, idf * (0.5 + confidence)))
            weighted.append(
                WeightedTerm(
                    value=decision.term.value,
                    weight=round(weight, 6),
                    origin=decision.term.origin,
                    reason="accepted_corpus_discriminative_term",
                    document_frequency=stat.document_frequency,
                    collection_frequency=stat.collection_frequency,
                )
            )
        expressions = [quoted for quoted in (_fts_quote(term.value) for term in weighted) if quoted]
        if not expressions:
            raise ValueError("sira_weighted_query_empty")
        if any(not math.isfinite(term.weight) or term.weight <= 0.0 for term in weighted):
            raise ValueError("sira_weighted_query_invalid_weight")
        return CompiledQuery(
            original_query=str(original_query or ""),
            match_expression=" OR ".join(expressions),
            terms=tuple(weighted),
            binding=binding,
            fallback_reason="expansion_empty" if len(weighted) == len(original_terms) else "",
        )
