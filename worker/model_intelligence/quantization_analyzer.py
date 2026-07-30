"""Static quantization metadata inspection without conversion or loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from worker.model_intelligence.common import (
    canonical_digest,
    read_bounded_json,
)


@dataclass(frozen=True)
class QuantizationAnalysis:
    schema_version: str
    status: str
    scheme: str | None
    bit_width: int | None
    group_size: int | None
    affected_tensors: tuple[str, ...]
    metadata_source: str | None
    reason_code: str | None

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "scheme": self.scheme,
            "bit_width": self.bit_width,
            "group_size": self.group_size,
            "affected_tensors": list(self.affected_tensors),
            "metadata_source": self.metadata_source,
            "reason_code": self.reason_code,
        }
        body["content_digest"] = canonical_digest(body)
        return body


class QuantizationAnalyzer:
    _CONFIG_NAMES = (
        "config.json",
        "quant_config.json",
        "quantize_config.json",
    )

    def __init__(
        self,
        *,
        max_json_bytes: int = 4 * 1024 * 1024,
        max_affected_tensors: int = 10_000,
    ) -> None:
        if max_json_bytes <= 0 or max_affected_tensors <= 0:
            raise ValueError("quantization analysis limits must be positive")
        self._max_json_bytes = max_json_bytes
        self._max_affected_tensors = max_affected_tensors

    def analyze(
        self,
        *,
        snapshot_root: str | Path,
        tensor_dtypes: Mapping[str, str] | None = None,
    ) -> QuantizationAnalysis:
        root = Path(snapshot_root).resolve(strict=True)
        metadata, source = self._configuration(root)
        quantization = metadata.get("quantization_config")
        if isinstance(quantization, Mapping):
            metadata = quantization
        scheme = self._first_string(
            metadata,
            ("quant_method", "quantization_method", "method", "scheme"),
        )
        bit_width = self._first_integer(
            metadata,
            ("bits", "bit_width", "w_bit"),
        )
        group_size = self._first_integer(
            metadata,
            ("group_size", "q_group_size"),
        )
        affected = self._affected_tensors(tensor_dtypes or {})
        if scheme is None and bit_width is None and not affected:
            return QuantizationAnalysis(
                schema_version="quantization_analysis.v1",
                status="not_available",
                scheme=None,
                bit_width=None,
                group_size=None,
                affected_tensors=(),
                metadata_source=source,
                reason_code="quantization_metadata_not_available",
            )
        if bit_width is not None and not 1 <= bit_width <= 16:
            return QuantizationAnalysis(
                schema_version="quantization_analysis.v1",
                status="failed",
                scheme=scheme,
                bit_width=bit_width,
                group_size=group_size,
                affected_tensors=affected,
                metadata_source=source,
                reason_code="quantization_metadata_inconsistent",
            )
        if group_size is not None and group_size <= 0:
            return QuantizationAnalysis(
                schema_version="quantization_analysis.v1",
                status="failed",
                scheme=scheme,
                bit_width=bit_width,
                group_size=group_size,
                affected_tensors=affected,
                metadata_source=source,
                reason_code="quantization_metadata_inconsistent",
            )
        return QuantizationAnalysis(
            schema_version="quantization_analysis.v1",
            status="available",
            scheme=scheme,
            bit_width=bit_width,
            group_size=group_size,
            affected_tensors=affected,
            metadata_source=source,
            reason_code=None,
        )

    def _configuration(
        self,
        root: Path,
    ) -> tuple[Mapping[str, object], str | None]:
        for relative_path in self._CONFIG_NAMES:
            if (root / relative_path).is_file():
                return (
                    read_bounded_json(
                        root,
                        relative_path,
                        max_bytes=self._max_json_bytes,
                    ),
                    relative_path,
                )
        return {}, None

    def _affected_tensors(
        self,
        tensor_dtypes: Mapping[str, str],
    ) -> tuple[str, ...]:
        quantized_dtypes = {
            "I4",
            "U4",
            "I8",
            "U8",
            "F8_E4M3",
            "F8_E5M2",
        }
        affected = tuple(
            sorted(
                name
                for name, dtype in tensor_dtypes.items()
                if dtype in quantized_dtypes
            )
        )
        return affected[: self._max_affected_tensors]

    @staticmethod
    def _first_string(
        value: Mapping[str, object],
        keys: Sequence[str],
    ) -> str | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    @staticmethod
    def _first_integer(
        value: Mapping[str, object],
        keys: Sequence[str],
    ) -> int | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
        return None
