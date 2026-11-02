"""
context_compression — Policy-gated, reversible context compression for Ananta agents.

Public API surface:

    ContextCompressionAdapter   — main entry point
    CompressionRequest          — input contract
    CompressionResult           — output contract
    build_compression_adapter   — convenience factory

    CompressionPolicyEngine     — evaluates compression policy
    CompressionPolicy           — declarative policy configuration

    TokenEstimator              — lightweight token estimation
    TokenMetrics                — per-text token/line/word stats

    CCRStore                    — content-addressable reference store
    CCREntry                    — single stored entry metadata

    SecretRedactor              — regex-based secret detection & redaction
    SensitivityLabel            — SAFE / SENSITIVE / SECRET / UNKNOWN

    SmartCompressor             — deterministic strategy-based compressor
    QualityGuard                — post-compression quality validation
    QualityResult               — quality check result
"""

from __future__ import annotations

from agent.services.context_compression.adapter import (
    CompressionRequest,
    CompressionResult,
    ContextCompressionAdapter,
    build_compression_adapter,
)
from agent.services.context_compression.ccr_store import (
    CCREntry,
    CCRStore,
)
from agent.services.context_compression.policy_engine import (
    CompressionPolicy,
    CompressionPolicyEngine,
)
from agent.services.context_compression.quality_guard import (
    QualityGuard,
    QualityResult,
)
from agent.services.context_compression.secret_redactor import (
    SecretRedactor,
    SensitivityLabel,
)
from agent.services.context_compression.smart_compressor import SmartCompressor
from agent.services.context_compression.token_estimator import (
    TokenEstimator,
    TokenMetrics,
)

__all__ = [
    # Adapter
    "ContextCompressionAdapter",
    "CompressionRequest",
    "CompressionResult",
    "build_compression_adapter",
    # Policy
    "CompressionPolicyEngine",
    "CompressionPolicy",
    # Token estimation
    "TokenEstimator",
    "TokenMetrics",
    # CCR store
    "CCRStore",
    "CCREntry",
    # Secret redaction
    "SecretRedactor",
    "SensitivityLabel",
    # Compressor
    "SmartCompressor",
    # Quality guard
    "QualityGuard",
    "QualityResult",
]
