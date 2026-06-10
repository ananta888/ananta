"""Deterministic domain/bounded-context discovery on CodeCompass outputs.

Pure analysis library (CCDD track): consumes index/details/relations/graph/
manifest outputs of the RAG helper and derives domain candidates with
evidence, coupling metrics and boundary warnings. It must not import hub or
agent runtime services.

Contract: docs/codecompass-domain-discovery.md
"""

from rag_helper.domain_discovery.contracts import (
    DOMAIN_ANALYSIS_SCHEMA,
    DOMAIN_COUPLING_SCHEMA,
    DomainCandidate,
)
from rag_helper.domain_discovery.inputs import AnalysisInputs, load_analysis_inputs

__all__ = [
    "DOMAIN_ANALYSIS_SCHEMA",
    "DOMAIN_COUPLING_SCHEMA",
    "DomainCandidate",
    "AnalysisInputs",
    "load_analysis_inputs",
]
