from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

UNCERTAIN_THRESHOLD = 0.3
CORROBORATION_BONUS = 0.05
MAX_CORROBORATION_BONUS = 0.15

class EvidenceType(str, Enum):
    AST_CALL = "ast_call"
    AST_IMPORT = "ast_import"
    TEST_REFERENCE = "test_reference"
    RUNTIME_CONFIG = "runtime_config"
    DOC_MENTION = "doc_mention"
    KEYWORD_MATCH = "keyword_match"
    HEURISTIC = "heuristic"
    LLM_ASSESSMENT = "llm_assessment"
    MANUAL = "manual"

EVIDENCE_BASE_CONFIDENCE: dict[str, float] = {
    EvidenceType.AST_CALL: 0.9,
    EvidenceType.AST_IMPORT: 0.85,
    EvidenceType.TEST_REFERENCE: 0.8,
    EvidenceType.RUNTIME_CONFIG: 0.75,
    EvidenceType.DOC_MENTION: 0.5,
    EvidenceType.KEYWORD_MATCH: 0.4,
    EvidenceType.HEURISTIC: 0.35,
    EvidenceType.LLM_ASSESSMENT: 0.3,
}

@dataclass
class EvidenceItem:
    evidence_type: EvidenceType
    source_file: str
    line: int | None
    confidence: float
    description: str

    def as_dict(self) -> dict:
        return {
            "evidence_type": self.evidence_type.value,
            "source_file": self.source_file,
            "line": self.line,
            "confidence": self.confidence,
            "description": self.description,
        }

@dataclass
class ContextItemEvidence:
    evidence_items: list[EvidenceItem]
    computed_confidence: float
    confidence_label: str
    corroboration_count: int
    is_uncertain: bool
    summary: str

class ContextEvidenceService:
    def compute_confidence(self, evidence_items: list[EvidenceItem]) -> float:
        if not evidence_items:
            return 0.0
        # LLM alone cannot exceed 0.3
        non_llm = [e for e in evidence_items if e.evidence_type != EvidenceType.LLM_ASSESSMENT]
        llm_only = not non_llm
        max_conf = max(e.confidence for e in evidence_items)
        if llm_only:
            return min(max_conf, 0.3)
        # Corroboration bonus based on distinct types (excluding LLM)
        distinct_types = len(set(e.evidence_type for e in non_llm))
        bonus = min(CORROBORATION_BONUS * (distinct_types - 1), MAX_CORROBORATION_BONUS)
        return min(1.0, max_conf + bonus)

    def label_confidence(self, confidence: float) -> str:
        if confidence >= 0.7:
            return "high"
        if confidence >= 0.4:
            return "medium"
        if confidence >= UNCERTAIN_THRESHOLD:
            return "low"
        return "uncertain"

    def build_evidence(self, evidence_items: list[EvidenceItem]) -> ContextItemEvidence:
        conf = self.compute_confidence(evidence_items)
        label = self.label_confidence(conf)
        distinct = len(set(e.evidence_type for e in evidence_items))
        return ContextItemEvidence(
            evidence_items=evidence_items,
            computed_confidence=round(conf, 4),
            confidence_label=label,
            corroboration_count=distinct,
            is_uncertain=conf < UNCERTAIN_THRESHOLD,
            summary=self.summarize_raw(evidence_items, conf, label),
        )

    def summarize_raw(self, items: list[EvidenceItem], conf: float, label: str) -> str:
        n = len(items)
        ast_count = sum(1 for e in items if e.evidence_type in (EvidenceType.AST_CALL, EvidenceType.AST_IMPORT))
        if ast_count:
            return f"Gefunden in {n} Quellen, davon {ast_count} via AST-Analyse (confidence: {label})"
        return f"Gefunden in {n} Quellen (confidence: {label})"

    def summarize(self, evidence: ContextItemEvidence) -> str:
        return evidence.summary

    def create_ast_evidence(self, source_file: str, line: int | None = None, description: str = "") -> EvidenceItem:
        return EvidenceItem(EvidenceType.AST_CALL, source_file, line, EVIDENCE_BASE_CONFIDENCE[EvidenceType.AST_CALL], description or "AST call reference")

    def create_heuristic_evidence(self, source_file: str, description: str = "") -> EvidenceItem:
        return EvidenceItem(EvidenceType.HEURISTIC, source_file, None, EVIDENCE_BASE_CONFIDENCE[EvidenceType.HEURISTIC], description or "heuristic")

    def create_llm_evidence(self, source_file: str, description: str = "") -> EvidenceItem:
        return EvidenceItem(EvidenceType.LLM_ASSESSMENT, source_file, None, EVIDENCE_BASE_CONFIDENCE[EvidenceType.LLM_ASSESSMENT], description or "LLM assessment")

    def default_evidence(self, provider: str, path: str) -> list[EvidenceItem]:
        return [self.create_heuristic_evidence(path, f"default evidence from {provider}")]
