"""TurboQuant encoding extensions — TQ-011 through TQ-014.

This module formalizes and extends the TurboQuant-inspired quantization
components that exist as private helpers in ``vector_encoding.py``. It does NOT
replace that module; all existing behaviour there remains unchanged.

TQ-011  DeterministicSignRotation   — proper class wrapping the private helper
TQ-012  TurboQuantMseEncoder        — PoC encoder class (formalization)
TQ-013  TurboQuantProdStub          — research-track stub, raises NotImplementedError
TQ-014  QuantizationFallbackPolicy  — structured fallback when quantized decode fails
"""
from __future__ import annotations

import base64
import hashlib
import logging
import math
import struct
import warnings
from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.vector_encoding import EncodedVector, VectorEncodingError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers (replicated from vector_encoding private scope so this
# module is self-contained without reaching into private symbols).
# ---------------------------------------------------------------------------

def _pack_floats(vector: list[float], fmt: str) -> bytes:
    if not vector:
        return b""
    return struct.pack("<" + fmt * len(vector), *vector)


def _scale_for(vector: list[float], *, levels: int) -> float:
    max_abs = max((abs(float(v)) for v in vector), default=0.0)
    if max_abs <= 1e-12:
        return 1.0
    return float(max_abs) / float(levels)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def _pack_signed_4bit(values: list[int]) -> bytes:
    nibbles = [(int(v) + 8) & 0x0F for v in values]
    out = bytearray()
    for i in range(0, len(nibbles), 2):
        first = nibbles[i]
        second = nibbles[i + 1] if i + 1 < len(nibbles) else 8
        out.append((first << 4) | second)
    return bytes(out)


def _unpack_signed_4bit(raw: bytes, dimensions: int) -> list[int]:
    values: list[int] = []
    for byte in raw:
        values.append(((byte >> 4) & 0x0F) - 8)
        if len(values) >= dimensions:
            break
        values.append((byte & 0x0F) - 8)
        if len(values) >= dimensions:
            break
    return values[:dimensions]


def _max_abs_error(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return max(abs(float(a) - float(b)) for a, b in zip(left, right, strict=False))


def _clean_vector(vector: list[float]) -> list[float]:
    clean: list[float] = []
    for value in list(vector or []):
        f = float(value)
        if not math.isfinite(f):
            raise VectorEncodingError("non_finite_vector_value")
        clean.append(f)
    return clean


# ---------------------------------------------------------------------------
# TQ-011: DeterministicSignRotation
# ---------------------------------------------------------------------------

class DeterministicSignRotation:
    """Deterministic per-dimension sign flip using a SHA-256-derived mask.

    This is a proper class wrapping the ``_deterministic_sign_rotation``
    private helper in ``vector_encoding.py`` (TQ-011).  The rotation is its
    own inverse: applying it twice recovers the original vector exactly
    (sign * sign == +1).

    The rotation is:
        sign_i = -1.0  if  sha256(f"{seed}:{i}")[0] & 1  else  +1.0
        rotated_i = original_i * sign_i

    Parameters
    ----------
    seed:
        Non-negative integer that uniquely identifies the rotation matrix.
        Must match between encode and decode.
    """

    def __init__(self, seed: int = 888) -> None:
        if int(seed) < 0:
            raise VectorEncodingError("invalid_sign_rotation_seed")
        self._seed = int(seed)
        # Cache signs lazily; indexed by dimension position.
        self._sign_cache: dict[int, float] = {}

    @property
    def seed(self) -> int:
        return self._seed

    def _sign_for(self, idx: int) -> float:
        if idx not in self._sign_cache:
            digest = hashlib.sha256(f"{self._seed}:{idx}".encode("utf-8")).digest()
            self._sign_cache[idx] = -1.0 if digest[0] & 1 else 1.0
        return self._sign_cache[idx]

    def apply(self, vector: list[float]) -> list[float]:
        """Apply the sign rotation.  Also serves as inverse (self-inverse)."""
        return [float(v) * self._sign_for(i) for i, v in enumerate(vector)]

    # Convenience aliases matching the paper description
    rotate = apply
    inverse = apply  # self-inverse

    def __repr__(self) -> str:
        return f"DeterministicSignRotation(seed={self._seed})"


# ---------------------------------------------------------------------------
# TQ-012: TurboQuantMseEncoder (formalization of turboquant_mse_experimental)
# ---------------------------------------------------------------------------

class TurboQuantMseEncoder:
    """PoC encoder: deterministic sign-rotation + symmetric 4-bit scalar quant.

    This is the formalization of the ``turboquant_mse_experimental`` mode
    defined in ``VectorEncoder`` (TQ-012).  It creates stable production seams
    for a real TurboQuant codebook / rotation later, without claiming to
    implement full TurboQuant.

    Encoding pipeline
    -----------------
    1. Validate and clean the input vector.
    2. Apply DeterministicSignRotation(seed) → rotated.
    3. Compute scale = max(|rotated|) / 7.
    4. Quantize each element to the range [-7, +7] (4-bit signed symmetric).
    5. Pack two 4-bit values per byte.
    6. Store scale + seed in metadata for lossless decode.

    Decode pipeline (exact inverse)
    ---------------------------------
    1. Unpack 4-bit values.
    2. Multiply by scale → rotated approximation.
    3. Apply DeterministicSignRotation(seed) again (self-inverse) → original
       approximation.

    Parameters
    ----------
    seed:
        Non-negative integer for the sign rotation.  Default: 888.
    levels:
        Quantization levels (symmetric).  Must be a power of 2 minus 1 for
        4-bit (i.e. 7).  Exposed for unit-testing; do not change in prod.
    """

    _MODE = "turboquant_mse_experimental"

    def __init__(self, seed: int = 888, levels: int = 7) -> None:
        if int(seed) < 0:
            raise VectorEncodingError("invalid_turboquant_mse_seed")
        if int(levels) < 1:
            raise VectorEncodingError("invalid_turboquant_mse_levels")
        self._seed = int(seed)
        self._levels = int(levels)
        self._rotation = DeterministicSignRotation(seed=self._seed)

    @property
    def seed(self) -> int:
        return self._seed

    def encode(self, vector: list[float]) -> EncodedVector:
        """Encode a float32 vector to a 4-bit turboquant_mse_experimental payload."""
        clean = _clean_vector(vector)
        if not clean:
            raise VectorEncodingError("turboquant_mse_encoder_empty_vector")

        rotated = self._rotation.apply(clean)
        scale = _scale_for(rotated, levels=self._levels)
        quants = [_clamp_int(round(v / scale), -self._levels, self._levels) for v in rotated]
        raw = _pack_signed_4bit(quants)

        # Compute max abs error for diagnostics
        decoded_rotated = [float(q) * scale for q in quants]
        decoded = self._rotation.apply(decoded_rotated)
        mae = _max_abs_error(clean, decoded)

        original_bytes = len(clean) * 4
        encoded_len = max(1, len(raw))

        payload = base64.b64encode(raw).decode("ascii")
        checksum = hashlib.sha256(_pack_floats(clean, "f")).hexdigest()[:24]

        metadata: dict[str, Any] = {
            "mode": self._MODE,
            "seed": self._seed,
            "levels": self._levels,
            "scale": scale,
            "zero_point": 0,
            "rotation": "deterministic_sign_rotation",
            "algorithm_version": "turboquant-mse-encoder.v1",
            "checksum": checksum,
        }
        diagnostics: dict[str, Any] = {
            "bytes_original_float32": original_bytes,
            "bytes_encoded_payload": len(raw),
            "compression_ratio_vs_float32": round(float(original_bytes) / float(encoded_len), 4),
            "max_abs_error": float(mae),
            "experimental": True,
            "experimental_warning": (
                "TQ-012 PoC: deterministic rotation + 4bit scalar quantization; "
                "not full TurboQuant_prod — see TQ-013 stub"
            ),
        }
        return EncodedVector(
            mode=self._MODE,
            dimensions=len(clean),
            payload=payload,
            metadata=metadata,
            diagnostics=diagnostics,
        )

    def decode(self, encoded: EncodedVector | dict[str, Any]) -> list[float]:
        """Decode a turboquant_mse_experimental payload back to float32 values."""
        item = (
            encoded
            if isinstance(encoded, EncodedVector)
            else EncodedVector.from_dict(dict(encoded or {}))
        )
        if item.mode != self._MODE:
            raise VectorEncodingError(
                f"turboquant_mse_encoder_mode_mismatch: expected {self._MODE!r}, got {item.mode!r}"
            )
        raw = base64.b64decode(item.payload.encode("ascii")) if item.payload else b""
        scale = float(item.metadata.get("scale") or 1.0)
        seed = int(item.metadata.get("seed") if item.metadata.get("seed") is not None else self._seed)
        rotation = DeterministicSignRotation(seed=seed)
        quants = _unpack_signed_4bit(raw, item.dimensions)
        rotated = [float(q) * scale for q in quants]
        return rotation.apply(rotated)

    def __repr__(self) -> str:
        return f"TurboQuantMseEncoder(seed={self._seed}, levels={self._levels})"


# ---------------------------------------------------------------------------
# TQ-013: TurboQuantProdStub
# ---------------------------------------------------------------------------

class TurboQuantProdStub:
    """Research-track stub for a future full TurboQuant production encoder.

    TQ-013: This class intentionally raises ``NotImplementedError`` for both
    ``encode()`` and ``decode()``.  It exists to:

    - Reserve the production API surface and import path.
    - Make it explicit in code review that full TurboQuant (codebook learning,
      product quantization, VQ-VAE integration) is a separate research track.
    - Prevent accidental use of the PoC encoder (TQ-012) as if it were
      production-grade.

    Do NOT implement this class without a separate research sign-off and a
    replacement for TQ-012 in vector_encoding.py.
    """

    provider_id: str = "turboquant_prod_stub"

    def encode(self, vector: list[float]) -> EncodedVector:  # noqa: ARG002
        raise NotImplementedError(
            "TQ-013: TurboQuantProdStub.encode() is not implemented. "
            "Full TurboQuant production (codebook learning, product quantization, "
            "VQ-VAE integration) is a research track that has not been signed off. "
            "Use TurboQuantMseEncoder (TQ-012) for the current PoC or "
            "VectorEncoder with mode='float32' for safe baseline encoding."
        )

    def decode(self, encoded: EncodedVector | dict[str, Any]) -> list[float]:  # noqa: ARG002
        raise NotImplementedError(
            "TQ-013: TurboQuantProdStub.decode() is not implemented. "
            "Full TurboQuant production (codebook learning, product quantization, "
            "VQ-VAE integration) is a research track that has not been signed off. "
            "Use TurboQuantMseEncoder (TQ-012) for the current PoC or "
            "VectorEncoder with mode='float32' for safe baseline decoding."
        )

    def __repr__(self) -> str:
        return "TurboQuantProdStub(NOT_IMPLEMENTED — research track only)"


# ---------------------------------------------------------------------------
# TQ-014: QuantizationFallbackPolicy
# ---------------------------------------------------------------------------

_VALID_MODES = frozenset({"block", "fallback_float32", "warn_only"})


@dataclass
class QuantizationFallbackPolicy:
    """Structured fallback policy when quantized decode fails (TQ-014).

    Modes
    -----
    ``"block"``
        Re-raise the original exception immediately.  No fallback attempted.
        Safe default for production where silent data corruption is unacceptable.

    ``"fallback_float32"``
        If ``fallback_float32_vector`` is provided (non-empty), return it as-is
        (the original unquantized float32 values).  If it is not provided or is
        empty, raise ``VectorEncodingError``.  Use when ``store_original=True``
        in VectorEncodingProfile and the original vector is available at the
        call site.

    ``"warn_only"``
        Log a warning at WARNING level and return the ``fallback_float32_vector``
        if available, else an empty list.  Never raises.  Only suitable for
        non-critical retrieval paths where partial results are acceptable.

    Parameters
    ----------
    mode:
        One of ``"block"``, ``"fallback_float32"``, ``"warn_only"``.
    label:
        Optional human-readable identifier for log messages (e.g. task ID).
    """

    mode: str = "block"
    label: str = ""

    def __post_init__(self) -> None:
        mode = str(self.mode or "block").strip().lower()
        if mode not in _VALID_MODES:
            raise VectorEncodingError(
                f"invalid_quantization_fallback_mode:{mode!r}; "
                f"valid modes are {sorted(_VALID_MODES)}"
            )
        object.__setattr__(self, "mode", mode)

    def handle(
        self,
        exc: Exception,
        encoded: EncodedVector | dict[str, Any] | None,
        fallback_float32_vector: list[float] | None = None,
    ) -> list[float]:
        """Handle a quantized decode failure according to the configured mode.

        Parameters
        ----------
        exc:
            The exception raised by the quantized decoder.
        encoded:
            The ``EncodedVector`` (or its dict representation) that failed to
            decode.  Used only for diagnostic logging.
        fallback_float32_vector:
            The original unquantized float32 vector, if available.  Required
            for ``"fallback_float32"`` mode; optional for ``"warn_only"``.

        Returns
        -------
        list[float]
            Decoded vector (either the fallback or an empty list in warn_only).

        Raises
        ------
        VectorEncodingError
            In ``"block"`` mode (always), or in ``"fallback_float32"`` mode
            when no fallback vector is available.
        Exception
            Re-raises the original exception in ``"block"`` mode.
        """
        label_prefix = f"[{self.label}] " if self.label else ""
        mode_str = str(self.mode)

        # Extract a short description of the encoded payload for logging.
        _mode_tag = ""
        if encoded is not None:
            if isinstance(encoded, EncodedVector):
                _mode_tag = encoded.mode
            elif isinstance(encoded, dict):
                _mode_tag = str(encoded.get("mode") or "")

        if mode_str == "block":
            log.error(
                "%sQuantizationFallbackPolicy(block): decode failed for mode=%r — re-raising. exc=%s",
                label_prefix,
                _mode_tag,
                exc,
            )
            raise exc

        if mode_str == "fallback_float32":
            if fallback_float32_vector:
                log.warning(
                    "%sQuantizationFallbackPolicy(fallback_float32): decode failed for mode=%r, "
                    "returning original float32 vector (%d dims). exc=%s",
                    label_prefix,
                    _mode_tag,
                    len(fallback_float32_vector),
                    exc,
                )
                return list(fallback_float32_vector)
            log.error(
                "%sQuantizationFallbackPolicy(fallback_float32): decode failed for mode=%r "
                "and no fallback_float32_vector provided — raising VectorEncodingError. exc=%s",
                label_prefix,
                _mode_tag,
                exc,
            )
            raise VectorEncodingError(
                f"quantization_fallback_float32_unavailable: decode failed and no original "
                f"float32 vector was provided. Original error: {exc}"
            ) from exc

        if mode_str == "warn_only":
            log.warning(
                "%sQuantizationFallbackPolicy(warn_only): decode failed for mode=%r — "
                "continuing with %s. exc=%s",
                label_prefix,
                _mode_tag,
                "fallback vector" if fallback_float32_vector else "empty list",
                exc,
            )
            warnings.warn(
                f"{label_prefix}quantization decode failed (warn_only mode), "
                f"mode={_mode_tag!r}: {exc}",
                stacklevel=3,
            )
            return list(fallback_float32_vector) if fallback_float32_vector else []

        # Unreachable due to __post_init__ validation; kept as a safety net.
        raise VectorEncodingError(f"unhandled_quantization_fallback_mode:{mode_str!r}")

    @classmethod
    def strict(cls, label: str = "") -> "QuantizationFallbackPolicy":
        """Factory: block mode (default for production)."""
        return cls(mode="block", label=label)

    @classmethod
    def with_float32_fallback(cls, label: str = "") -> "QuantizationFallbackPolicy":
        """Factory: fallback_float32 mode."""
        return cls(mode="fallback_float32", label=label)

    @classmethod
    def lenient(cls, label: str = "") -> "QuantizationFallbackPolicy":
        """Factory: warn_only mode (non-critical retrieval paths only)."""
        return cls(mode="warn_only", label=label)

    def __repr__(self) -> str:
        label_part = f", label={self.label!r}" if self.label else ""
        return f"QuantizationFallbackPolicy(mode={self.mode!r}{label_part})"
