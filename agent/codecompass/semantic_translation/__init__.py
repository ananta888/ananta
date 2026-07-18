"""Deterministic Semantic Translation Graph support for CodeCompass.

This package intentionally contains only deterministic parsers, registries,
transformers and verifiers. LLMs may propose candidates elsewhere, but this
module never treats free-form text as source of truth.
"""

from agent.codecompass.semantic_translation.config import SemanticTranslationConfig, load_semantic_translation_config
from agent.codecompass.semantic_translation.registry import (
    SemanticAdapterRegistry,
    SemanticExecutionPort,
    SemanticGraphExecutionPort,
    SemanticParseExecutionPort,
    get_semantic_adapter_registry,
)
from agent.codecompass.semantic_translation.transform import DeterministicTransformEngine
from agent.codecompass.semantic_translation.verifier import SemanticTranslationVerifier

__all__ = [
    "DeterministicTransformEngine",
    "SemanticTranslationConfig",
    "SemanticTranslationVerifier",
    "SemanticAdapterRegistry",
    "SemanticExecutionPort",
    "SemanticGraphExecutionPort",
    "SemanticParseExecutionPort",
    "get_semantic_adapter_registry",
    "load_semantic_translation_config",
]
