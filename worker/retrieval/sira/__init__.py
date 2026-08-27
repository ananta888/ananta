"""SIRA-inspired corpus-discriminative retrieval worker components.

The package implements Ananta-owned contracts and algorithms.  It does not
vendor the facebookresearch/sira runtime or create an additional control
plane; the Hub selects the profile and a delegated Worker executes it.
"""

from worker.retrieval.sira.config import SiraConfig, SiraMode
from worker.retrieval.sira.contracts import (
    CompiledQuery,
    CorpusBinding,
    GeneratedTerm,
    QueryExpansion,
    TermDecision,
)

__all__ = [
    "CompiledQuery",
    "CorpusBinding",
    "GeneratedTerm",
    "QueryExpansion",
    "SiraConfig",
    "SiraMode",
    "TermDecision",
]
