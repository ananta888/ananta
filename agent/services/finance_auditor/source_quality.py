"""Evidence quality checks that never invent source identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.services.finance_auditor.models import SourceReference, SourceType

_VALID_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9_.:-]+$")
_QUALITY = {
    SourceType.REGULATOR: 1.0,
    SourceType.OFFICIAL_REPORT: 0.9,
    SourceType.ACADEMIC: 0.85,
    SourceType.ESTABLISHED_MEDIA: 0.65,
    SourceType.BANK_RESEARCH: 0.5,
    SourceType.COMPANY_PR: 0.35,
    SourceType.INFLUENCER: 0.2,
    SourceType.FORUM: 0.1,
    SourceType.UNKNOWN: 0.0,
}
_CONFLICT_TYPES = {SourceType.COMPANY_PR, SourceType.BANK_RESEARCH, SourceType.INFLUENCER, SourceType.FORUM}


@dataclass(frozen=True)
class SourceAssessment:
    confidence: float
    evidence_notes: tuple[str, ...]
    strong_grounding: bool


def assess_sources(sources: tuple[SourceReference, ...]) -> SourceAssessment:
    if not sources:
        return SourceAssessment(0.2, ("No sources were provided; factual claims remain unverified.",), False)
    notes: list[str] = []
    usable_scores: list[float] = []
    strong = False
    for source in sources:
        if not _VALID_SOURCE_ID.fullmatch(source.source_id):
            notes.append(
                f"Unverified source identifier '{source.source_id}' was not used for "
                "grounding; expected a provided SRC_* identifier."
            )
            continue
        score = _QUALITY[source.source_type]
        usable_scores.append(score)
        if source.source_type in _CONFLICT_TYPES or source.conflict_disclosed:
            notes.append(f"{source.source_id} ({source.source_type.value}) has a potential interest conflict.")
        else:
            notes.append(f"{source.source_id} assessed as {source.source_type.value} evidence.")
        strong = strong or source.source_type in {SourceType.REGULATOR, SourceType.OFFICIAL_REPORT, SourceType.ACADEMIC}
    if not usable_scores:
        return SourceAssessment(0.1, tuple(notes), False)
    return SourceAssessment(round(sum(usable_scores) / len(usable_scores), 2), tuple(notes), strong)
