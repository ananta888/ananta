from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass
from typing import Any

_ALLOWED_MODES = {
    "off",
    "float32",
    "float16",
    "int8",
    "symmetric4bit",
    "turboquant_mse_experimental",
}
_EXPERIMENTAL_MODES = {"symmetric4bit", "turboquant_mse_experimental"}


class VectorEncodingError(ValueError):
    """Raised when a vector cannot be encoded or decoded safely."""


@dataclass(frozen=True, slots=True)
class VectorEncodingProfile:
    """Stable, auditable encoding profile for CodeCompass vectors.

    Embedding providers create semantic vectors. This layer decides how Ananta
    stores, replays and diagnoses those vectors. That split is the important
    architectural point: model/agent output is an input signal, not authority.
    """

    mode: str = "off"
    target_bits: float = 32.0
    seed: int = 888
    block_size: int = 0
    store_original: bool = False
    algorithm_version: str = "vector-encoding.v1"

    def __post_init__(self) -> None:
        mode = str(self.mode or "off").strip().lower()
        if mode not in _ALLOWED_MODES:
            raise VectorEncodingError(f"unsupported_vector_encoding_mode:{mode}")
        if float(self.target_bits) <= 0:
            raise VectorEncodingError("invalid_vector_encoding_target_bits")
        if int(self.seed) < 0:
            raise VectorEncodingError("invalid_vector_encoding_seed")
        if int(self.block_size) < 0:
            raise VectorEncodingError("invalid_vector_encoding_block_size")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "target_bits", float(self.target_bits))
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "block_size", int(self.block_size))
        object.__setattr__(self, "store_original", bool(self.store_original))
        object.__setattr__(self, "algorithm_version", str(self.algorithm_version or "vector-encoding.v1"))

    @property
    def enabled(self) -> bool:
        return self.mode not in {"off", "float32"}

    @property
    def experimental(self) -> bool:
        return self.mode in _EXPERIMENTAL_MODES

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target_bits": self.target_bits,
            "seed": self.seed,
            "block_size": self.block_size,
            "store_original": self.store_original,
            "algorithm_version": self.algorithm_version,
            "experimental": self.experimental,
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def disabled(cls) -> "VectorEncodingProfile":
        return cls(mode="off", target_bits=32.0)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "VectorEncodingProfile":
        cfg = dict(config or {})
        env = os.environ
        return cls(
            mode=str(cfg.get("mode") or env.get("CODECOMPASS_VECTOR_ENCODING_MODE") or "off"),
            target_bits=float(cfg.get("target_bits") or env.get("CODECOMPASS_VECTOR_ENCODING_TARGET_BITS") or 32.0),
            seed=int(cfg.get("seed") or env.get("CODECOMPASS_VECTOR_ENCODING_SEED") or 888),
            block_size=int(cfg.get("block_size") or env.get("CODECOMPASS_VECTOR_ENCODING_BLOCK_SIZE") or 0),
            store_original=_bool(cfg.get("store_original", env.get("CODECOMPASS_VECTOR_ENCODING_STORE_ORIGINAL", False))),
            algorithm_version=str(cfg.get("algorithm_version") or "vector-encoding.v1"),
        )


@dataclass(frozen=True, slots=True)
class EncodedVector:
    mode: str
    dimensions: int
    payload: str
    metadata: dict[str, Any]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "dimensions": self.dimensions,
            "payload": self.payload,
            "metadata": dict(self.metadata),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EncodedVector":
        return cls(
            mode=str(value.get("mode") or "off"),
            dimensions=int(value.get("dimensions") or 0),
            payload=str(value.get("payload") or ""),
            metadata=dict(value.get("metadata") or {}),
            diagnostics=dict(value.get("diagnostics") or {}),
        )


class VectorEncoder:
    def __init__(self, profile: VectorEncodingProfile | None = None) -> None:
        self.profile = profile or VectorEncodingProfile.disabled()

    def encode(self, vector: list[float]) -> EncodedVector:
        clean = _clean_vector(vector)
        mode = self.profile.mode
        if mode in {"off", "float32"}:
            return self._encode_float32(clean, mode=mode)
        if mode == "float16":
            return self._encode_float16(clean)
        if mode == "int8":
            return self._encode_int8(clean)
        if mode == "symmetric4bit":
            return self._encode_symmetric4bit(clean)
        if mode == "turboquant_mse_experimental":
            return self._encode_turboquant_mse_experimental(clean)
        raise VectorEncodingError(f"unsupported_vector_encoding_mode:{mode}")

    def decode(self, encoded: EncodedVector | dict[str, Any]) -> list[float]:
        item = encoded if isinstance(encoded, EncodedVector) else EncodedVector.from_dict(dict(encoded or {}))
        mode = str(item.mode or "off").lower()
        if mode in {"off", "float32"}:
            return _unpack_floats(item.payload, "f", item.dimensions)
        if mode == "float16":
            return _decode_float16_payload(item.payload, item.dimensions)
        if mode == "int8":
            return self._decode_int8(item)
        if mode == "symmetric4bit":
            return self._decode_symmetric4bit(item)
        if mode == "turboquant_mse_experimental":
            return self._decode_turboquant_mse_experimental(item)
        raise VectorEncodingError(f"unsupported_vector_encoding_mode:{mode}")

    def encode_many(self, vectors: list[list[float]]) -> list[EncodedVector]:
        return [self.encode(vector) for vector in vectors]

    def _base_meta(self, vector: list[float]) -> dict[str, Any]:
        return {
            "profile": self.profile.as_dict(),
            "profile_hash": self.profile.config_hash(),
            "checksum": _checksum_floats(vector),
        }

    def _diagnostics(self, vector: list[float], encoded_bytes: bytes, *, max_abs_error: float = 0.0) -> dict[str, Any]:
        original_bytes = len(vector) * 4
        encoded_len = max(1, len(encoded_bytes))
        return {
            "bytes_original_float32": original_bytes,
            "bytes_encoded_payload": len(encoded_bytes),
            "compression_ratio_vs_float32": round(float(original_bytes) / float(encoded_len), 4) if encoded_len else 1.0,
            "max_abs_error": float(max_abs_error),
            "experimental": self.profile.experimental,
        }

    def _encode_float32(self, vector: list[float], *, mode: str) -> EncodedVector:
        packed = _pack_floats(vector, "f")
        return EncodedVector(
            mode=mode,
            dimensions=len(vector),
            payload=base64.b64encode(packed).decode("ascii"),
            metadata=self._base_meta(vector),
            diagnostics=self._diagnostics(vector, packed, max_abs_error=0.0),
        )

    def _encode_float16(self, vector: list[float]) -> EncodedVector:
        packed = _pack_float16(vector)
        decoded = _decode_float16_payload(base64.b64encode(packed).decode("ascii"), len(vector))
        return EncodedVector(
            mode="float16",
            dimensions=len(vector),
            payload=base64.b64encode(packed).decode("ascii"),
            metadata=self._base_meta(vector),
            diagnostics=self._diagnostics(vector, packed, max_abs_error=_max_abs_error(vector, decoded)),
        )

    def _encode_int8(self, vector: list[float]) -> EncodedVector:
        scale = _scale_for(vector, levels=127)
        raw = bytes(_clamp_int(round(v / scale), -127, 127) & 0xFF for v in vector)
        decoded = [float(_signed_byte(b)) * scale for b in raw]
        meta = {**self._base_meta(vector), "scale": scale, "zero_point": 0, "levels": 127}
        return EncodedVector(
            mode="int8",
            dimensions=len(vector),
            payload=base64.b64encode(raw).decode("ascii"),
            metadata=meta,
            diagnostics=self._diagnostics(vector, raw, max_abs_error=_max_abs_error(vector, decoded)),
        )

    def _decode_int8(self, item: EncodedVector) -> list[float]:
        raw = base64.b64decode(item.payload.encode("ascii")) if item.payload else b""
        scale = float(item.metadata.get("scale") or 1.0)
        return [float(_signed_byte(b)) * scale for b in raw[: item.dimensions]]

    def _encode_symmetric4bit(self, vector: list[float]) -> EncodedVector:
        scale = _scale_for(vector, levels=7)
        quants = [_clamp_int(round(v / scale), -7, 7) for v in vector]
        raw = _pack_signed_4bit(quants)
        decoded = [float(q) * scale for q in quants]
        meta = {**self._base_meta(vector), "scale": scale, "zero_point": 0, "levels": 7}
        return EncodedVector(
            mode="symmetric4bit",
            dimensions=len(vector),
            payload=base64.b64encode(raw).decode("ascii"),
            metadata=meta,
            diagnostics={
                **self._diagnostics(vector, raw, max_abs_error=_max_abs_error(vector, decoded)),
                "experimental_warning": "symmetric4bit may change retrieval ranking; keep fallback diagnostics enabled",
            },
        )

    def _decode_symmetric4bit(self, item: EncodedVector) -> list[float]:
        raw = base64.b64decode(item.payload.encode("ascii")) if item.payload else b""
        scale = float(item.metadata.get("scale") or 1.0)
        quants = _unpack_signed_4bit(raw, item.dimensions)
        return [float(q) * scale for q in quants]

    def _encode_turboquant_mse_experimental(self, vector: list[float]) -> EncodedVector:
        # This is a deliberately honest bridge, not a fake paper implementation:
        # deterministic sign-flip rotation + symmetric4bit quantization. It creates
        # the production seams for a real TurboQuant codebook/rotation later.
        rotated = _deterministic_sign_rotation(vector, self.profile.seed)
        scale = _scale_for(rotated, levels=7)
        quants = [_clamp_int(round(v / scale), -7, 7) for v in rotated]
        raw = _pack_signed_4bit(quants)
        decoded_rotated = [float(q) * scale for q in quants]
        decoded = _deterministic_sign_rotation(decoded_rotated, self.profile.seed)
        meta = {
            **self._base_meta(vector),
            "scale": scale,
            "zero_point": 0,
            "levels": 7,
            "rotation": "deterministic_sign_rotation",
            "seed": self.profile.seed,
        }
        return EncodedVector(
            mode="turboquant_mse_experimental",
            dimensions=len(vector),
            payload=base64.b64encode(raw).decode("ascii"),
            metadata=meta,
            diagnostics={
                **self._diagnostics(vector, raw, max_abs_error=_max_abs_error(vector, decoded)),
                "experimental_warning": "TurboQuant-inspired seam only: deterministic rotation + 4bit scalar quantization, not full TurboQuant_prod",
            },
        )

    def _decode_turboquant_mse_experimental(self, item: EncodedVector) -> list[float]:
        raw = base64.b64decode(item.payload.encode("ascii")) if item.payload else b""
        scale = float(item.metadata.get("scale") or 1.0)
        seed = int(item.metadata.get("seed") or self.profile.seed)
        quants = _unpack_signed_4bit(raw, item.dimensions)
        rotated = [float(q) * scale for q in quants]
        return _deterministic_sign_rotation(rotated, seed)


def build_vector_encoder(config: dict[str, Any] | None = None) -> VectorEncoder:
    return VectorEncoder(VectorEncodingProfile.from_config(config))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_vector(vector: list[float]) -> list[float]:
    clean: list[float] = []
    for value in list(vector or []):
        f = float(value)
        if not math.isfinite(f):
            raise VectorEncodingError("non_finite_vector_value")
        clean.append(f)
    return clean


def _pack_floats(vector: list[float], fmt: str) -> bytes:
    if not vector:
        return b""
    return struct.pack("<" + fmt * len(vector), *vector)


def _unpack_floats(payload: str, fmt: str, dimensions: int) -> list[float]:
    raw = base64.b64decode(payload.encode("ascii")) if payload else b""
    if not raw:
        return []
    size = struct.calcsize(fmt)
    count = min(int(dimensions), len(raw) // size)
    return [float(v) for v in struct.unpack("<" + fmt * count, raw[: count * size])]


def _pack_float16(vector: list[float]) -> bytes:
    try:
        return struct.pack("<" + "e" * len(vector), *vector)
    except struct.error as exc:
        raise VectorEncodingError(f"float16_encoding_failed:{exc}") from exc


def _decode_float16_payload(payload: str, dimensions: int) -> list[float]:
    raw = base64.b64decode(payload.encode("ascii")) if payload else b""
    if not raw:
        return []
    count = min(int(dimensions), len(raw) // 2)
    return [float(v) for v in struct.unpack("<" + "e" * count, raw[: count * 2])]


def _scale_for(vector: list[float], *, levels: int) -> float:
    max_abs = max((abs(float(v)) for v in vector), default=0.0)
    if max_abs <= 1e-12:
        return 1.0
    return float(max_abs) / float(levels)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def _signed_byte(value: int) -> int:
    return int(value) - 256 if int(value) > 127 else int(value)


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


def _checksum_floats(vector: list[float]) -> str:
    return hashlib.sha256(_pack_floats(vector, "f")).hexdigest()[:24]


def _max_abs_error(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return max(abs(float(a) - float(b)) for a, b in zip(left, right, strict=False))


def _deterministic_sign_rotation(vector: list[float], seed: int) -> list[float]:
    rotated: list[float] = []
    for idx, value in enumerate(vector):
        digest = hashlib.sha256(f"{seed}:{idx}".encode("utf-8")).digest()
        sign = -1.0 if digest[0] & 1 else 1.0
        rotated.append(float(value) * sign)
    return rotated
