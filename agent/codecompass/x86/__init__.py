"""CodeCompass x86 Assembly Core Extension.

Provides static analysis of x86/x86-64 assembly: indexing, CFG,
call graph, and query tools. No binary execution; static analysis only.
"""
from __future__ import annotations

__all__ = [
    "config",
    "diagnostics",
    "safety",
    "input_taxonomy",
    "models",
    "adapter",
    "fixture_adapter",
    "capstone_adapter",
    "index_builder",
    "index_pipeline",
    "graph_extensions",
    "query",
    "viewer",
]
