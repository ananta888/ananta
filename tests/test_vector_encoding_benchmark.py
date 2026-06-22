"""TQ-023 — Quantization benchmark quality gates for VectorEncoder.

These tests act as measurable quality gates:
- compression ratios for each mode must meet minimum thresholds
- max_abs_error must stay within tolerance for precision-sensitive modes
- cosine similarity between original and decoded must exceed minimum bounds
- all modes must encode/decode without error (regression guard)

Test vectors are reproducible: random.Random(42).gauss(0, 1), then L2-normalized.
"""
from __future__ import annotations

import math
import random

import pytest

from worker.retrieval.vector_encoding import (
    VectorEncoder,
    VectorEncodingProfile,
)

DIMS = 384  # all-MiniLM-L6-v2 dimensions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_vector(dims: int = DIMS, seed: int = 42) -> list[float]:
    """L2-normalized Gaussian random vector, fully reproducible."""
    rng = random.Random(seed)
    raw = [rng.gauss(0.0, 1.0) for _ in range(dims)]
    norm = math.sqrt(sum(x * x for x in raw))
    if norm < 1e-12:
        return [0.0] * dims
    return [x / norm for x in raw]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def _make_encoder(mode: str) -> VectorEncoder:
    return VectorEncoder(VectorEncodingProfile(mode=mode))


def _encode_decode(mode: str, vector: list[float]) -> tuple[list[float], float]:
    """Return (decoded, compression_ratio)."""
    encoder = _make_encoder(mode)
    ev = encoder.encode(vector)
    decoded = encoder.decode(ev)
    ratio = float(ev.diagnostics.get("compression_ratio_vs_float32") or 1.0)
    return decoded, ratio


# ---------------------------------------------------------------------------
# Compression ratio gates
# ---------------------------------------------------------------------------

def test_float16_compression_ratio_vs_float32():
    """float16 must achieve ~2x compression vs float32."""
    vector = _make_test_vector()
    _, ratio = _encode_decode("float16", vector)
    assert ratio >= 1.9, f"float16 compression ratio too low: {ratio}"
    assert ratio <= 2.1, f"float16 compression ratio unexpectedly high: {ratio}"


def test_int8_compression_ratio_vs_float32():
    """int8 must achieve ~4x compression vs float32."""
    vector = _make_test_vector()
    _, ratio = _encode_decode("int8", vector)
    assert ratio >= 3.9, f"int8 compression ratio too low: {ratio}"
    assert ratio <= 4.1, f"int8 compression ratio unexpectedly high: {ratio}"


def test_symmetric4bit_compression_ratio_vs_float32():
    """symmetric4bit must achieve ~8x compression vs float32."""
    vector = _make_test_vector()
    _, ratio = _encode_decode("symmetric4bit", vector)
    assert ratio >= 7.5, f"symmetric4bit compression ratio too low: {ratio}"
    assert ratio <= 8.5, f"symmetric4bit compression ratio unexpectedly high: {ratio}"


def test_turboquant_mse_compression_ratio():
    """turboquant_mse_experimental must achieve ~8x compression (4-bit packing)."""
    vector = _make_test_vector()
    _, ratio = _encode_decode("turboquant_mse_experimental", vector)
    assert ratio >= 7.5, f"turboquant_mse_experimental compression ratio too low: {ratio}"
    assert ratio <= 8.5, f"turboquant_mse_experimental compression ratio unexpectedly high: {ratio}"


# ---------------------------------------------------------------------------
# Precision / max_abs_error gates
# ---------------------------------------------------------------------------

def test_float16_max_abs_error_within_tolerance():
    """float16 max_abs_error must be < 0.001 for a normalized unit vector."""
    encoder = _make_encoder("float16")
    vector = _make_test_vector()
    ev = encoder.encode(vector)
    max_err = float(ev.diagnostics.get("max_abs_error") or 0.0)
    assert max_err < 0.001, f"float16 max_abs_error too large: {max_err}"


def test_int8_max_abs_error_within_tolerance():
    """int8 max_abs_error must be < 0.01 for a normalized unit vector."""
    encoder = _make_encoder("int8")
    vector = _make_test_vector()
    ev = encoder.encode(vector)
    max_err = float(ev.diagnostics.get("max_abs_error") or 0.0)
    assert max_err < 0.01, f"int8 max_abs_error too large: {max_err}"


# ---------------------------------------------------------------------------
# Cosine similarity preservation gates
# ---------------------------------------------------------------------------

def test_int8_cosine_similarity_preserved():
    """Cosine similarity between original and int8-decoded vector must be ≥ 0.99."""
    vector = _make_test_vector()
    decoded, _ = _encode_decode("int8", vector)
    sim = _cosine_similarity(vector, decoded)
    assert sim >= 0.99, f"int8 cosine similarity too low: {sim:.6f}"


def test_float16_cosine_similarity_preserved():
    """Cosine similarity between original and float16-decoded vector must be ≥ 0.9999."""
    vector = _make_test_vector()
    decoded, _ = _encode_decode("float16", vector)
    sim = _cosine_similarity(vector, decoded)
    assert sim >= 0.9999, f"float16 cosine similarity too low: {sim:.6f}"


# ---------------------------------------------------------------------------
# All-modes round-trip (regression guard)
# ---------------------------------------------------------------------------

_ALL_MODES = [
    "off",
    "float32",
    "float16",
    "int8",
    "symmetric4bit",
    "turboquant_mse_experimental",
]


@pytest.mark.parametrize("mode", _ALL_MODES)
def test_benchmark_all_modes_encode_decode_roundtrip(mode):
    """Every supported mode must encode and decode a 384-dim vector without error."""
    encoder = _make_encoder(mode)
    vector = _make_test_vector()
    ev = encoder.encode(vector)
    decoded = encoder.decode(ev)
    assert len(decoded) == DIMS, f"mode={mode} roundtrip changed dimensions: {len(decoded)}"
    # decoded values must all be finite
    assert all(math.isfinite(v) for v in decoded), f"mode={mode} produced non-finite decoded values"


# ---------------------------------------------------------------------------
# mode=off is lossless
# ---------------------------------------------------------------------------

def test_encoding_mode_off_is_lossless():
    """mode=off stores values as float32 binary; diagnostics must report max_abs_error=0.0
    (no additional quantization beyond float32 precision) and the decoded vector must
    match the original to within float32 rounding tolerance (~1e-7 for normalized vectors).
    """
    encoder = _make_encoder("off")
    vector = _make_test_vector()
    ev = encoder.encode(vector)

    # The encoder itself reports no intentional quantization error
    max_err = float(ev.diagnostics.get("max_abs_error") or 0.0)
    assert max_err == 0.0, f"mode=off diagnostics report non-zero quantization error: {max_err}"

    # Actual float64→float32→float64 round-trip: differences are only float32 epsilon
    decoded = encoder.decode(ev)
    assert len(decoded) == DIMS
    for orig, dec in zip(vector, decoded, strict=False):
        diff = abs(orig - dec)
        assert diff < 1e-6, (
            f"mode=off value diverges beyond float32 precision: orig={orig}, decoded={dec}, diff={diff}"
        )
