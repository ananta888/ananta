"""Single source of immutable default weights for CodeCompass ranking."""

from __future__ import annotations

from types import MappingProxyType

UNIVERSAL_SOURCE_WEIGHTS = MappingProxyType({
    "path_lexical": 0.17,
    "filename_lexical": 0.15,
    "symbol_lexical": 0.24,
    "exact_symbol": 0.14,
    "structural_role": 0.08,
    "entrypoint": 0.06,
    "centrality": 0.06,
    "graph_proximity": 0.06,
    "role_penalty": -1.0,
    "diversity_penalty": 1.0,
})

HYBRID_SCORE_WEIGHTS = MappingProxyType({
    "embedding_score": 0.45,
    "graph_score": 0.20,
    "symbol_score": 0.20,
    "transformer_rerank_score": 0.0,
    "policy_penalty": -0.20,
})

HYBRID_TRANSFORMER_WEIGHTS = MappingProxyType({
    "embedding_score": 0.30,
    "graph_score": 0.15,
    "symbol_score": 0.15,
    "transformer_rerank_score": 0.40,
    "policy_penalty": -0.20,
})

WORKER_CHANNEL_WEIGHTS = MappingProxyType({
    "debugging": MappingProxyType({
        "dense": 0.28, "lexical": 0.20, "symbol": 0.14,
        "codecompass_fts": 0.20, "codecompass_vector": 0.10,
        "codecompass_graph": 0.08,
    }),
    "implementation": MappingProxyType({
        "dense": 0.24, "lexical": 0.16, "symbol": 0.20,
        "codecompass_fts": 0.18, "codecompass_vector": 0.12,
        "codecompass_graph": 0.10,
    }),
    "architecture": MappingProxyType({
        "dense": 0.20, "lexical": 0.22, "symbol": 0.12,
        "codecompass_fts": 0.20, "codecompass_vector": 0.14,
        "codecompass_graph": 0.12,
    }),
})

WORKER_TASK_PROFILE_ALIASES = MappingProxyType({
    "bugfix": "debugging",
    "feature": "implementation",
    "bootstrap": "architecture",
})
