"""Bounded static Safetensors topology inspection without tensor loading."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from typing import Mapping

from worker.model_intelligence.common import (
    ModelAnalysisError,
    canonical_digest,
    open_bounded_file,
)


_DTYPE_BITS: Mapping[str, int] = {
    "BOOL": 8,
    "I4": 4,
    "U4": 4,
    "I8": 8,
    "U8": 8,
    "I16": 16,
    "U16": 16,
    "I32": 32,
    "U32": 32,
    "I64": 64,
    "U64": 64,
    "F8_E4M3": 8,
    "F8_E5M2": 8,
    "F16": 16,
    "BF16": 16,
    "F32": 32,
    "F64": 64,
}
_LAYER_PATTERN = re.compile(
    r"(?:^|\\.)(?:layers?|blocks?|h)\\.(?P<index>[0-9]+)(?:\\.|$)"
)


@dataclass(frozen=True)
class TensorFact:
    name: str
    module: str
    layer_index: int | None
    dtype: str
    shape: tuple[int, ...]
    parameter_count: int
    size_bytes: int
    relative_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "module": self.module,
            "layer_index": self.layer_index,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "parameter_count": self.parameter_count,
            "size_bytes": self.size_bytes,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True)
class StaticTensorAnalysis:
    schema_version: str
    status: str
    tensor_count: int
    parameter_count: int
    total_tensor_bytes: int
    dtypes: Mapping[str, int]
    modules: Mapping[str, int]
    tensors: tuple[TensorFact, ...]

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "tensor_count": self.tensor_count,
            "parameter_count": self.parameter_count,
            "total_tensor_bytes": self.total_tensor_bytes,
            "dtypes": dict(sorted(self.dtypes.items())),
            "modules": dict(sorted(self.modules.items())),
            "tensors": [item.to_dict() for item in self.tensors],
        }
        body["content_digest"] = canonical_digest(body)
        return body


@dataclass(frozen=True)
class StaticTensorAnalysisPolicy:
    max_header_bytes: int = 16 * 1024 * 1024
    max_tensors: int = 100_000
    max_rank: int = 32
    max_parameters: int = 10_000_000_000_000

    def __post_init__(self) -> None:
        if min(
            self.max_header_bytes,
            self.max_tensors,
            self.max_rank,
            self.max_parameters,
        ) <= 0:
            raise ValueError("static tensor analysis limits must be positive")


class StaticTensorAnalyzer:
    def __init__(
        self,
        policy: StaticTensorAnalysisPolicy | None = None,
    ) -> None:
        self._policy = policy or StaticTensorAnalysisPolicy()

    def analyze(
        self,
        *,
        snapshot_root: str | Path,
        weight_files: tuple[str, ...],
    ) -> StaticTensorAnalysis:
        if not weight_files:
            raise ModelAnalysisError(
                "static_analysis_weights_missing",
                "At least one admitted Safetensors file is required.",
            )
        tensors: list[TensorFact] = []
        for relative_path in sorted(set(weight_files)):
            if not relative_path.lower().endswith(".safetensors"):
                raise ModelAnalysisError(
                    "unsupported_format",
                    "Static tensor inspection currently supports Safetensors.",
                    relative_path=relative_path,
                )
            tensors.extend(
                self._inspect_safetensors(snapshot_root, relative_path)
            )
            if len(tensors) > self._policy.max_tensors:
                raise ModelAnalysisError(
                    "static_analysis_tensor_count_exceeded",
                    "Tensor count exceeds the configured limit.",
                )

        ordered = tuple(
            sorted(tensors, key=lambda item: (item.name, item.relative_path))
        )
        parameter_count = sum(item.parameter_count for item in ordered)
        if parameter_count > self._policy.max_parameters:
            raise ModelAnalysisError(
                "static_analysis_parameter_count_exceeded",
                "Parameter count exceeds the configured limit.",
            )
        dtype_counts: dict[str, int] = {}
        module_counts: dict[str, int] = {}
        for item in ordered:
            dtype_counts[item.dtype] = dtype_counts.get(item.dtype, 0) + 1
            module_counts[item.module] = module_counts.get(item.module, 0) + 1
        return StaticTensorAnalysis(
            schema_version="static_analysis.v1",
            status="available",
            tensor_count=len(ordered),
            parameter_count=parameter_count,
            total_tensor_bytes=sum(item.size_bytes for item in ordered),
            dtypes=dtype_counts,
            modules=module_counts,
            tensors=ordered,
        )

    def _inspect_safetensors(
        self,
        snapshot_root: str | Path,
        relative_path: str,
    ) -> list[TensorFact]:
        descriptor, file_size = open_bounded_file(
            snapshot_root,
            relative_path,
            max_bytes=2**63 - 1,
        )
        try:
            prefix = os.read(descriptor, 8)
            if len(prefix) != 8:
                raise ModelAnalysisError(
                    "safetensors_header_missing",
                    "Safetensors header prefix is incomplete.",
                    relative_path=relative_path,
                )
            header_size = int.from_bytes(prefix, "little", signed=False)
            if not 2 <= header_size <= self._policy.max_header_bytes:
                raise ModelAnalysisError(
                    "safetensors_header_size_invalid",
                    "Safetensors header size is outside the configured limit.",
                    relative_path=relative_path,
                )
            header_bytes = os.read(descriptor, header_size)
            if len(header_bytes) != header_size:
                raise ModelAnalysisError(
                    "safetensors_header_incomplete",
                    "Safetensors header is incomplete.",
                    relative_path=relative_path,
                )
        finally:
            os.close(descriptor)
        try:
            header = json.loads(header_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelAnalysisError(
                "safetensors_header_invalid",
                "Safetensors header is not valid UTF-8 JSON.",
                relative_path=relative_path,
            ) from exc
        if not isinstance(header, Mapping):
            raise ModelAnalysisError(
                "safetensors_header_type_invalid",
                "Safetensors header must be an object.",
                relative_path=relative_path,
            )

        data_size = file_size - 8 - header_size
        intervals: list[tuple[int, int, str]] = []
        facts: list[TensorFact] = []
        for name, raw in sorted(header.items()):
            if name == "__metadata__":
                if not isinstance(raw, Mapping):
                    raise ModelAnalysisError(
                        "safetensors_metadata_invalid",
                        "Safetensors metadata must be an object.",
                        relative_path=relative_path,
                    )
                continue
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(raw, Mapping)
            ):
                raise ModelAnalysisError(
                    "safetensors_tensor_entry_invalid",
                    "Safetensors tensor entries must be named objects.",
                    relative_path=relative_path,
                )
            dtype = str(raw.get("dtype") or "")
            raw_shape = raw.get("shape")
            offsets = raw.get("data_offsets")
            if dtype not in _DTYPE_BITS:
                raise ModelAnalysisError(
                    "safetensors_dtype_unsupported",
                    "Safetensors dtype is unsupported.",
                    relative_path=relative_path,
                )
            if (
                not isinstance(raw_shape, list)
                or len(raw_shape) > self._policy.max_rank
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or item < 0
                    for item in raw_shape
                )
            ):
                raise ModelAnalysisError(
                    "safetensors_shape_invalid",
                    "Safetensors shape is invalid.",
                    relative_path=relative_path,
                )
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or item < 0
                    for item in offsets
                )
            ):
                raise ModelAnalysisError(
                    "safetensors_offsets_invalid",
                    "Safetensors offsets are invalid.",
                    relative_path=relative_path,
                )
            start, end = offsets
            if start > end or end > data_size:
                raise ModelAnalysisError(
                    "safetensors_offsets_out_of_bounds",
                    "Safetensors offsets exceed the file payload.",
                    relative_path=relative_path,
                )
            parameter_count = math.prod(raw_shape)
            size_bytes = end - start
            expected_bytes = (
                parameter_count * _DTYPE_BITS[dtype] + 7
            ) // 8
            if size_bytes != expected_bytes:
                raise ModelAnalysisError(
                    "safetensors_tensor_size_mismatch",
                    "Tensor shape, dtype, and payload size disagree.",
                    relative_path=relative_path,
                )
            intervals.append((start, end, name))
            layer_match = _LAYER_PATTERN.search(name)
            module = name.rsplit(".", 1)[0] if "." in name else name
            facts.append(
                TensorFact(
                    name=name,
                    module=module,
                    layer_index=(
                        int(layer_match.group("index"))
                        if layer_match is not None
                        else None
                    ),
                    dtype=dtype,
                    shape=tuple(raw_shape),
                    parameter_count=parameter_count,
                    size_bytes=size_bytes,
                    relative_path=relative_path,
                )
            )
        for previous, current in zip(
            sorted(intervals),
            sorted(intervals)[1:],
        ):
            if previous[1] > current[0]:
                raise ModelAnalysisError(
                    "safetensors_tensor_overlap",
                    "Safetensors tensor payload ranges overlap.",
                    relative_path=relative_path,
                )
        return facts
