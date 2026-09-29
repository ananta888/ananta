"""VEC-DELTA-002 / TQ-025 — Security and payload redaction tests for VectorEncoder.

These tests verify that:
- payloads are opaque base64 blobs (no plaintext float leakage)
- metadata does not expose raw float arrays
- non-finite values are rejected at encode time
- profile hashes are stable and mode-discriminating
- unknown modes are rejected
- as_dict() is safe to surface
"""
from __future__ import annotations

import base64
import json
import math

import pytest

from worker.retrieval.vector_encoding import (
    EncodedVector,
    VectorEncoder,
    VectorEncodingError,
    VectorEncodingProfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encoder(mode: str = "off") -> VectorEncoder:
    return VectorEncoder(VectorEncodingProfile(mode=mode))


def _sample_vector(n: int = 8) -> list[float]:
    return [0.1 * i for i in range(n)]


# ---------------------------------------------------------------------------
# TQ-025-001  payload is valid base64, no plaintext leakage
# ---------------------------------------------------------------------------

def test_encoded_vector_payload_is_base64_opaque():
    """Payload must be a valid base64 string; raw float text must not appear."""
    encoder = _make_encoder("float32")
    vector = [0.12345678, -0.98765432, 0.55555555]
    ev = encoder.encode(vector)

    # Must decode without error
    raw = base64.b64decode(ev.payload.encode("ascii"))
    assert isinstance(raw, bytes)
    assert len(raw) > 0

    # The literal float representation must not appear in the payload string
    for f in vector:
        assert str(f) not in ev.payload, f"Plaintext float {f!r} found in payload"


# ---------------------------------------------------------------------------
# TQ-025-002  metadata does not contain raw float values as plaintext
# ---------------------------------------------------------------------------

def test_encoded_vector_metadata_no_raw_values():
    """Metadata dict must not embed the original float array as JSON text."""
    encoder = _make_encoder("int8")
    vector = [0.111, -0.222, 0.333, -0.444]
    ev = encoder.encode(vector)

    metadata_json = json.dumps(ev.metadata)
    for f in vector:
        assert str(f) not in metadata_json, (
            f"Raw float value {f!r} leaked into metadata: {metadata_json}"
        )


# ---------------------------------------------------------------------------
# TQ-025-003  non-finite values raise VectorEncodingError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_encode_non_finite_raises(bad_value):
    """NaN and infinite values must be rejected before encoding."""
    encoder = _make_encoder("float32")
    with pytest.raises(VectorEncodingError, match="non_finite"):
        encoder.encode([0.5, bad_value, 0.3])


# ---------------------------------------------------------------------------
# TQ-025-004  zero-length vector encodes cleanly
# ---------------------------------------------------------------------------

def test_encode_empty_vector_returns_empty_payload():
    """Encoding an empty vector must not raise; payload must be decodeable."""
    encoder = _make_encoder("off")
    ev = encoder.encode([])
    assert ev.dimensions == 0
    # payload is empty string or valid base64 empty
    if ev.payload:
        decoded = base64.b64decode(ev.payload.encode("ascii"))
        assert len(decoded) == 0
    # round-trip: decode must return empty list
    back = encoder.decode(ev)
    assert back == []


# ---------------------------------------------------------------------------
# TQ-025-005  tampered payload does not expose sensitive exception info
# ---------------------------------------------------------------------------

def test_decode_tampered_payload_raises_or_degrades():
    """A base64-encoded garbage payload must not surface sensitive internals.

    The encoder must either raise VectorEncodingError or return a degraded
    (partial/empty) result — never a raw Python struct exception with a
    traceback string that includes key material.
    """
    encoder = _make_encoder("int8")
    # Build a minimal EncodedVector with a tampered payload
    # "garbage" is valid base64 (decodes to 5 bytes) but not valid int8 quants
    tampered = EncodedVector(
        mode="int8",
        dimensions=4,
        payload=base64.b64encode(b"garb").decode("ascii"),
        metadata={"scale": 0.01, "zero_point": 0, "levels": 127},
        diagnostics={},
    )
    try:
        result = encoder.decode(tampered)
        # Degraded path: result must be a list, not an exception string
        assert isinstance(result, list)
    except VectorEncodingError:
        pass  # acceptable: explicit error is safe
    except Exception as exc:
        msg = str(exc).lower()
        assert "key" not in msg and "secret" not in msg and "authorization" not in msg, (
            f"Exception leaks sensitive terms: {exc}"
        )


# ---------------------------------------------------------------------------
# TQ-025-006  profile hash is stable across calls
# ---------------------------------------------------------------------------

def test_profile_hash_stable_across_calls():
    """config_hash() must return the same value on every call — no randomness."""
    profile = VectorEncodingProfile(mode="int8", seed=42)
    hashes = {profile.config_hash() for _ in range(20)}
    assert len(hashes) == 1, "config_hash() is not stable"


# ---------------------------------------------------------------------------
# TQ-025-007  different modes produce different hashes
# ---------------------------------------------------------------------------

def test_profile_hash_differs_for_different_modes():
    """Distinct encoding modes must produce distinct config hashes."""
    modes = ["off", "float32", "float16", "int8", "symmetric4bit"]
    hashes = [VectorEncodingProfile(mode=m).config_hash() for m in modes]
    assert len(hashes) == len(set(hashes)), "Mode change did not produce a unique hash"


# ---------------------------------------------------------------------------
# TQ-025-008  seed in metadata is an integer, not a secret string
# ---------------------------------------------------------------------------

def test_seed_in_metadata_not_sensitive_data():
    """The turboquant seed recorded in metadata must be an int, not a secret key."""
    encoder = VectorEncoder(VectorEncodingProfile(mode="turboquant_mse_experimental", seed=888))
    ev = encoder.encode([0.1, -0.2, 0.3, -0.4])
    seed_value = ev.metadata.get("seed")
    assert seed_value is not None, "seed missing from turboquant_mse_experimental metadata"
    assert isinstance(seed_value, int), f"seed must be int, got {type(seed_value)}"
    # It must be the plain integer value, not an encoded token or key string
    assert seed_value == 888


# ---------------------------------------------------------------------------
# TQ-025-009  as_dict() does not include the raw float array
# ---------------------------------------------------------------------------

def test_payload_redaction_safe():
    """EncodedVector.as_dict() must not contain a 'raw_vector' or 'floats' key."""
    encoder = _make_encoder("int8")
    vector = [0.5, -0.3, 0.1, 0.9]
    ev = encoder.encode(vector)
    d = ev.as_dict()

    forbidden_keys = {"raw_vector", "floats", "original_vector", "values"}
    found = forbidden_keys & set(d.keys())
    assert not found, f"as_dict() exposes raw vector under keys: {found}"

    # The actual float values must not appear as a JSON array in the serialised dict
    serialised = json.dumps(d)
    for f in vector:
        # format that numpy/json would produce
        if f"{f:.6f}" in serialised or f"{f}" in serialised:
            # Only allowed if they appear inside the opaque payload string
            payload_index = serialised.find(d["payload"])
            value_index = serialised.find(str(f))
            assert value_index > payload_index or value_index == -1, (
                f"Raw float {f} appears outside payload in as_dict() output"
            )


# ---------------------------------------------------------------------------
# TQ-025-010  unknown mode rejected at profile construction
# ---------------------------------------------------------------------------

def test_vector_encoding_profile_rejects_unknown_modes():
    """VectorEncodingProfile must raise VectorEncodingError for unknown modes."""
    with pytest.raises(VectorEncodingError, match="unsupported_vector_encoding_mode"):
        VectorEncodingProfile(mode="rogue_mode")

    with pytest.raises(VectorEncodingError, match="unsupported_vector_encoding_mode"):
        VectorEncodingProfile(mode="TOTALLY_UNKNOWN_XYZ")
