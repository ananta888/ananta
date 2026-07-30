"""Bounded, non-mutating PEFT/LoRA delta analysis for admitted snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from agent.services.model_intelligence_snapshot_admission import (
    AdmittedAnalysisSnapshot,
    AnalysisSnapshotFile,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LORA_TENSOR_NAME = re.compile(
    r"^(?P<module>.+)\.lora_(?P<side>A|B)(?:\.[A-Za-z0-9_-]+)?\.weight$"
)
_METRIC_TOLERANCES = MappingProxyType(
    {
        "coverage_absolute": 0.0,
        "norm_absolute": 1e-10,
        "parameter_count_absolute": 0.0,
    }
)


class LoraDeltaAnalysisError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ImmutableBaseModelIdentity:
    model_id: str
    revision: str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.model_id, "base_model_identity_invalid", maximum=512)
        _require_text(self.revision, "base_model_identity_invalid", maximum=256)
        _require_sha256(self.content_sha256, "base_model_identity_invalid")

    def to_dict(self) -> dict[str, str]:
        return {
            "content_sha256": self.content_sha256,
            "model_id": self.model_id,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ImmutableAdapterIdentity:
    adapter_id: str
    revision: str
    content_sha256: str
    base_model_id: str
    base_model_content_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.adapter_id, "adapter_identity_invalid", maximum=512)
        _require_text(self.revision, "adapter_identity_invalid", maximum=256)
        _require_text(self.base_model_id, "adapter_identity_invalid", maximum=512)
        _require_sha256(self.content_sha256, "adapter_identity_invalid")
        _require_sha256(
            self.base_model_content_sha256,
            "adapter_identity_invalid",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "base_model_content_sha256": self.base_model_content_sha256,
            "base_model_id": self.base_model_id,
            "content_sha256": self.content_sha256,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class LoraNumericTensor:
    name: str
    shape: tuple[int, ...]
    values: tuple[float, ...]
    dtype: str = "float32"

    def __post_init__(self) -> None:
        _require_text(self.name, "adapter_tensor_invalid", maximum=1024)
        if (
            not self.shape
            or any(
                isinstance(dimension, bool)
                or not isinstance(dimension, int)
                or dimension <= 0
                for dimension in self.shape
            )
        ):
            raise LoraDeltaAnalysisError(
                "adapter_tensor_invalid",
                "adapter tensor shape is invalid",
            )
        if math.prod(self.shape) != len(self.values):
            raise LoraDeltaAnalysisError(
                "adapter_tensor_invalid",
                "adapter tensor values do not match its shape",
            )
        if any(not math.isfinite(float(value)) for value in self.values):
            raise LoraDeltaAnalysisError(
                "adapter_tensor_non_finite",
                "adapter tensor contains a non-finite value",
            )


@dataclass(frozen=True)
class LoraAdapterPayload:
    config: Mapping[str, Any]
    tensors: tuple[LoraNumericTensor, ...]

    def __post_init__(self) -> None:
        names = tuple(tensor.name for tensor in self.tensors)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise LoraDeltaAnalysisError(
                "adapter_tensor_inventory_invalid",
                "adapter tensors must be uniquely sorted",
            )
        object.__setattr__(
            self,
            "config",
            MappingProxyType(dict(self.config)),
        )


class LoraAdapterReaderPort(Protocol):
    def read(self, snapshot: AdmittedAnalysisSnapshot) -> LoraAdapterPayload: ...


@dataclass(frozen=True)
class SafetensorsLoraReaderPolicy:
    max_config_bytes: int = 1024 * 1024
    max_tensor_count: int = 4096
    max_tensor_elements: int = 8_000_000
    max_total_elements: int = 16_000_000

    def __post_init__(self) -> None:
        if (
            self.max_config_bytes <= 0
            or self.max_tensor_count <= 0
            or self.max_tensor_elements <= 0
            or self.max_total_elements <= 0
            or self.max_tensor_elements > self.max_total_elements
        ):
            raise ValueError("LoRA reader bounds are invalid")


class SafetensorsLoraAdapterReader(LoraAdapterReaderPort):
    """Read only the two admitted PEFT files; never instantiate a model."""

    def __init__(
        self,
        policy: SafetensorsLoraReaderPolicy | None = None,
    ) -> None:
        self._policy = policy or SafetensorsLoraReaderPolicy()

    def read(self, snapshot: AdmittedAnalysisSnapshot) -> LoraAdapterPayload:
        config_entry = _unique_admitted_file(snapshot, "adapter_config.json")
        weights_entry = _unique_admitted_file(
            snapshot,
            "adapter_model.safetensors",
        )
        config_path = _resolve_admitted_file(snapshot, config_entry)
        weights_path = _resolve_admitted_file(snapshot, weights_entry)
        config_bytes = _read_and_verify(config_path, config_entry)
        if len(config_bytes) > self._policy.max_config_bytes:
            raise LoraDeltaAnalysisError(
                "adapter_config_too_large",
                "adapter config exceeds its analysis bound",
            )
        try:
            config = json.loads(config_bytes.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LoraDeltaAnalysisError(
                "adapter_config_invalid",
                "adapter config is not valid JSON",
            ) from exc
        if not isinstance(config, dict):
            raise LoraDeltaAnalysisError(
                "adapter_config_invalid",
                "adapter config must be an object",
            )
        _verify_file_digest(weights_path, weights_entry)
        try:
            from safetensors import safe_open  # type: ignore[import]
        except ImportError as exc:
            raise LoraDeltaAnalysisError(
                "safetensors_runtime_unavailable",
                "safetensors is required for LoRA delta analysis",
            ) from exc

        tensors: list[LoraNumericTensor] = []
        total_elements = 0
        try:
            with safe_open(
                str(weights_path),
                framework="np",
                device="cpu",
            ) as handle:
                names = sorted(handle.keys())
                if not names or len(names) > self._policy.max_tensor_count:
                    raise LoraDeltaAnalysisError(
                        "adapter_tensor_count_invalid",
                        "adapter tensor count exceeds its analysis bound",
                    )
                for name in names:
                    raw = handle.get_tensor(name)
                    shape = tuple(int(dimension) for dimension in raw.shape)
                    element_count = math.prod(shape)
                    total_elements += element_count
                    if (
                        element_count > self._policy.max_tensor_elements
                        or total_elements > self._policy.max_total_elements
                    ):
                        raise LoraDeltaAnalysisError(
                            "adapter_tensor_budget_exceeded",
                            "adapter tensors exceed their analysis element budget",
                        )
                    values = tuple(
                        float(value)
                        for value in raw.astype("float64", copy=False).reshape(-1)
                    )
                    tensors.append(
                        LoraNumericTensor(
                            name=str(name),
                            shape=shape,
                            values=values,
                            dtype=str(raw.dtype),
                        )
                    )
        except LoraDeltaAnalysisError:
            raise
        except Exception as exc:
            raise LoraDeltaAnalysisError(
                "adapter_safetensors_invalid",
                "adapter safetensors could not be read safely",
            ) from exc
        _verify_file_digest(weights_path, weights_entry)
        return LoraAdapterPayload(
            config=config,
            tensors=tuple(tensors),
        )


@dataclass(frozen=True)
class LoraDeltaAnalysisPolicy:
    max_modules: int = 2048
    max_rank: int = 512
    max_gram_operations: int = 50_000_000

    def __post_init__(self) -> None:
        if (
            self.max_modules <= 0
            or self.max_rank <= 0
            or self.max_gram_operations <= 0
        ):
            raise ValueError("LoRA delta analysis bounds are invalid")


@dataclass(frozen=True)
class LoraModuleDeltaMetrics:
    module: str
    rank: int
    alpha: float
    scaling: float
    parameter_count: int
    lora_a_frobenius_norm: float
    lora_b_frobenius_norm: float
    delta_frobenius_norm: float
    scaled_delta_frobenius_norm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "delta_frobenius_norm": self.delta_frobenius_norm,
            "lora_a_frobenius_norm": self.lora_a_frobenius_norm,
            "lora_b_frobenius_norm": self.lora_b_frobenius_norm,
            "module": self.module,
            "parameter_count": self.parameter_count,
            "rank": self.rank,
            "scaled_delta_frobenius_norm": self.scaled_delta_frobenius_norm,
            "scaling": self.scaling,
        }


@dataclass(frozen=True)
class LoraDeltaAnalysis:
    base_model: ImmutableBaseModelIdentity
    adapter: ImmutableAdapterIdentity
    modules: tuple[LoraModuleDeltaMetrics, ...]
    configured_targets: tuple[str, ...]
    matched_targets: tuple[str, ...]
    parameter_count: int
    aggregate_delta_frobenius_norm: float
    aggregate_scaled_delta_frobenius_norm: float
    composition_support: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "adapter": self.adapter.to_dict(),
            "aggregate_delta_frobenius_norm": self.aggregate_delta_frobenius_norm,
            "aggregate_scaled_delta_frobenius_norm": self.aggregate_scaled_delta_frobenius_norm,
            "base_model": self.base_model.to_dict(),
            "composition_support": {
                name: dict(value)
                for name, value in sorted(self.composition_support.items())
            },
            "metric_tolerances": dict(_METRIC_TOLERANCES),
            "module_coverage": {
                "affected_module_count": len(self.modules),
                "configured_target_count": len(self.configured_targets),
                "matched_target_count": len(self.matched_targets),
                "matched_targets": list(self.matched_targets),
                "target_pattern_ratio": _stable_float(
                    len(self.matched_targets) / len(self.configured_targets)
                ),
            },
            "modules": [module.to_dict() for module in self.modules],
            "parameter_count": self.parameter_count,
            "schema": "ananta.model-intelligence.lora-delta.v1",
        }
        body["content_digest"] = hashlib.sha256(
            _canonical_json(body),
        ).hexdigest()
        return body


class LoraDeltaAnalyzer:
    """Analyze admitted adapter tensors without loading or mutating a base model."""

    def __init__(
        self,
        *,
        reader: LoraAdapterReaderPort,
        policy: LoraDeltaAnalysisPolicy | None = None,
    ) -> None:
        self._reader = reader
        self._policy = policy or LoraDeltaAnalysisPolicy()

    def analyze(
        self,
        *,
        snapshot: AdmittedAnalysisSnapshot,
        base_model: ImmutableBaseModelIdentity,
        adapter: ImmutableAdapterIdentity,
    ) -> LoraDeltaAnalysis:
        self._verify_identities(snapshot, base_model, adapter)
        payload = self._reader.read(snapshot)
        config = payload.config
        self._verify_composition(config)
        configured_base = str(
            config.get("base_model_name_or_path")
            or config.get("base_model")
            or config.get("base_model_id")
            or ""
        ).strip()
        configured_revision = str(
            config.get("revision")
            or config.get("base_model_revision")
            or ""
        ).strip()
        if (
            configured_base != base_model.model_id
            or (
                configured_revision
                and configured_revision != base_model.revision
            )
        ):
            raise LoraDeltaAnalysisError(
                "incompatible_base_model",
                "adapter config is not bound to the supplied base model",
            )

        configured_targets = _target_modules(config.get("target_modules"))
        rank_default = _positive_integer(config.get("r"), "adapter_rank_invalid")
        alpha_default = _positive_number(
            config.get("lora_alpha"),
            "adapter_alpha_invalid",
        )
        rank_pattern = _numeric_pattern(
            config.get("rank_pattern"),
            integer=True,
            reason_code="adapter_rank_pattern_invalid",
        )
        alpha_pattern = _numeric_pattern(
            config.get("alpha_pattern"),
            integer=False,
            reason_code="adapter_alpha_pattern_invalid",
        )
        pairs = self._tensor_pairs(payload.tensors)
        matched_targets = tuple(
            target
            for target in configured_targets
            if any(_module_matches(module, target) for module in pairs)
        )
        if len(matched_targets) != len(configured_targets):
            raise LoraDeltaAnalysisError(
                "adapter_target_module_missing",
                "one or more configured target modules have no LoRA tensors",
            )

        use_rslora = bool(config.get("use_rslora", False))
        modules: list[LoraModuleDeltaMetrics] = []
        for module, pair in sorted(pairs.items()):
            lora_a, lora_b = pair["A"], pair["B"]
            if len(lora_a.shape) != 2 or len(lora_b.shape) != 2:
                raise LoraDeltaAnalysisError(
                    "adapter_tensor_rank_invalid",
                    "LoRA A and B tensors must be matrices",
                )
            rank, input_width = lora_a.shape
            output_width, b_rank = lora_b.shape
            expected_rank = int(
                _pattern_value(rank_pattern, module, rank_default)
            )
            if (
                rank != b_rank
                or rank != expected_rank
                or rank > self._policy.max_rank
            ):
                raise LoraDeltaAnalysisError(
                    "adapter_rank_mismatch",
                    "LoRA tensor rank does not match adapter config",
                )
            operations = rank * rank * (input_width + output_width)
            if operations > self._policy.max_gram_operations:
                raise LoraDeltaAnalysisError(
                    "delta_analysis_budget_exceeded",
                    "LoRA delta norm exceeds its computation budget",
                )
            alpha = float(
                _pattern_value(alpha_pattern, module, alpha_default)
            )
            scaling = alpha / (math.sqrt(rank) if use_rslora else rank)
            a_norm, b_norm, delta_norm = _factor_and_delta_norms(
                lora_a,
                lora_b,
            )
            modules.append(
                LoraModuleDeltaMetrics(
                    module=module,
                    rank=rank,
                    alpha=_stable_float(alpha),
                    scaling=_stable_float(scaling),
                    parameter_count=len(lora_a.values) + len(lora_b.values),
                    lora_a_frobenius_norm=_stable_float(a_norm),
                    lora_b_frobenius_norm=_stable_float(b_norm),
                    delta_frobenius_norm=_stable_float(delta_norm),
                    scaled_delta_frobenius_norm=_stable_float(
                        abs(scaling) * delta_norm
                    ),
                )
            )
        module_rows = tuple(modules)
        return LoraDeltaAnalysis(
            base_model=base_model,
            adapter=adapter,
            modules=module_rows,
            configured_targets=configured_targets,
            matched_targets=matched_targets,
            parameter_count=sum(item.parameter_count for item in module_rows),
            aggregate_delta_frobenius_norm=_stable_float(
                math.sqrt(
                    math.fsum(
                        item.delta_frobenius_norm**2
                        for item in module_rows
                    )
                )
            ),
            aggregate_scaled_delta_frobenius_norm=_stable_float(
                math.sqrt(
                    math.fsum(
                        item.scaled_delta_frobenius_norm**2
                        for item in module_rows
                    )
                )
            ),
            composition_support=MappingProxyType(
                _composition_support(config)
            ),
        )

    @staticmethod
    def _verify_identities(
        snapshot: AdmittedAnalysisSnapshot,
        base_model: ImmutableBaseModelIdentity,
        adapter: ImmutableAdapterIdentity,
    ) -> None:
        if (
            adapter.base_model_id != base_model.model_id
            or adapter.base_model_content_sha256 != base_model.content_sha256
        ):
            raise LoraDeltaAnalysisError(
                "incompatible_base_model",
                "adapter identity is not bound to the supplied base model",
            )
        if not hmac.compare_digest(
            adapter.content_sha256,
            snapshot.manifest.snapshot_digest,
        ):
            raise LoraDeltaAnalysisError(
                "adapter_snapshot_identity_mismatch",
                "adapter identity does not match the admitted snapshot",
            )

    def _tensor_pairs(
        self,
        tensors: tuple[LoraNumericTensor, ...],
    ) -> dict[str, dict[str, LoraNumericTensor]]:
        pairs: dict[str, dict[str, LoraNumericTensor]] = {}
        for tensor in tensors:
            match = _LORA_TENSOR_NAME.fullmatch(tensor.name)
            if match is None:
                raise LoraDeltaAnalysisError(
                    "unsupported_adapter_composition",
                    "adapter contains a tensor outside plain LoRA A/B factors",
                )
            module = match.group("module")
            side = match.group("side")
            pair = pairs.setdefault(module, {})
            if side in pair:
                raise LoraDeltaAnalysisError(
                    "adapter_module_duplicate",
                    "adapter contains duplicate LoRA factors",
                )
            pair[side] = tensor
        if not pairs or len(pairs) > self._policy.max_modules:
            raise LoraDeltaAnalysisError(
                "adapter_modules_missing",
                "adapter has no bounded LoRA module inventory",
            )
        if any(set(pair) != {"A", "B"} for pair in pairs.values()):
            raise LoraDeltaAnalysisError(
                "adapter_module_pair_missing",
                "every LoRA module requires one A and one B tensor",
            )
        return pairs

    @staticmethod
    def _verify_composition(config: Mapping[str, Any]) -> None:
        if str(config.get("peft_type") or "LORA").strip().upper() != "LORA":
            raise LoraDeltaAnalysisError(
                "unsupported_adapter_composition",
                "only plain PEFT LoRA adapters are supported",
            )
        if (
            bool(config.get("use_dora", False))
            or str(config.get("bias") or "none").strip().lower() != "none"
            or config.get("modules_to_save")
        ):
            raise LoraDeltaAnalysisError(
                "unsupported_adapter_composition",
                "DoRA, saved modules and trainable bias require separate analyzers",
            )


def _factor_and_delta_norms(
    lora_a: LoraNumericTensor,
    lora_b: LoraNumericTensor,
) -> tuple[float, float, float]:
    rank, input_width = lora_a.shape
    output_width, _ = lora_b.shape
    a_values = lora_a.values
    b_values = lora_b.values
    gram_a = [
        [
            math.fsum(
                a_values[left * input_width + column]
                * a_values[right * input_width + column]
                for column in range(input_width)
            )
            for right in range(rank)
        ]
        for left in range(rank)
    ]
    gram_b = [
        [
            math.fsum(
                b_values[row * rank + left]
                * b_values[row * rank + right]
                for row in range(output_width)
            )
            for right in range(rank)
        ]
        for left in range(rank)
    ]
    delta_squared = math.fsum(
        gram_a[left][right] * gram_b[left][right]
        for left in range(rank)
        for right in range(rank)
    )
    if delta_squared < 0 and abs(delta_squared) <= 1e-12:
        delta_squared = 0.0
    if delta_squared < 0 or not math.isfinite(delta_squared):
        raise LoraDeltaAnalysisError(
            "delta_norm_invalid",
            "LoRA delta norm is not finite",
        )
    return (
        math.sqrt(math.fsum(value * value for value in a_values)),
        math.sqrt(math.fsum(value * value for value in b_values)),
        math.sqrt(delta_squared),
    )


def _target_modules(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise LoraDeltaAnalysisError(
            "adapter_target_modules_invalid",
            "adapter target_modules must be a non-empty list",
        )
    targets = tuple(sorted({str(item).strip() for item in value}))
    if (
        len(targets) != len(value)
        or any(
            not target
            or len(target) > 512
            or any(character.isspace() for character in target)
            for target in targets
        )
    ):
        raise LoraDeltaAnalysisError(
            "adapter_target_modules_invalid",
            "adapter target modules are invalid",
        )
    return targets


def _numeric_pattern(
    value: Any,
    *,
    integer: bool,
    reason_code: str,
) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 4096:
        raise LoraDeltaAnalysisError(
            reason_code,
            "adapter pattern is invalid",
        )
    result: dict[str, float] = {}
    for raw_key, raw_number in value.items():
        key = str(raw_key).strip()
        if not key or len(key) > 512:
            raise LoraDeltaAnalysisError(reason_code, "adapter pattern key is invalid")
        number = (
            float(_positive_integer(raw_number, reason_code))
            if integer
            else _positive_number(raw_number, reason_code)
        )
        result[key] = number
    return result


def _pattern_value(
    pattern: Mapping[str, float],
    module: str,
    default: int | float,
) -> float:
    matches = [
        (key, value)
        for key, value in pattern.items()
        if _module_matches(module, key)
    ]
    if not matches:
        return float(default)
    return max(matches, key=lambda item: (len(item[0]), item[0]))[1]


def _module_matches(module: str, target: str) -> bool:
    return module == target or module.endswith(f".{target}")


def _composition_support(config: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    quantization = config.get("quantization_config")
    qlora: dict[str, Any]
    if isinstance(quantization, Mapping) and (
        quantization.get("load_in_4bit") is True
        or quantization.get("load_in_8bit") is True
        or quantization.get("bits") in {4, 8}
    ):
        allowed_keys = (
            "bits",
            "bnb_4bit_compute_dtype",
            "bnb_4bit_quant_type",
            "load_in_4bit",
            "load_in_8bit",
        )
        qlora = {
            "metadata": {
                key: quantization[key]
                for key in allowed_keys
                if key in quantization
                and isinstance(quantization[key], (bool, int, float, str))
            },
            "status": "available",
        }
    else:
        qlora = {
            "reason_code": "base_quantization_not_proven",
            "status": "unsupported",
        }
    return {
        "merged_delta": {
            "reason_code": "non_mutating_factor_analysis",
            "status": "not_run",
        },
        "qlora_metadata": qlora,
    }


def _unique_admitted_file(
    snapshot: AdmittedAnalysisSnapshot,
    filename: str,
) -> AnalysisSnapshotFile:
    matches = tuple(
        item
        for item in snapshot.manifest.files
        if PurePosixPath(item.relative_path).name == filename
    )
    if len(matches) != 1:
        raise LoraDeltaAnalysisError(
            "adapter_required_file_missing",
            f"admitted adapter requires exactly one {filename}",
        )
    return matches[0]


def _resolve_admitted_file(
    snapshot: AdmittedAnalysisSnapshot,
    entry: AnalysisSnapshotFile,
) -> Path:
    root = snapshot.verified_snapshot.root.resolve(strict=True)
    candidate = root / entry.relative_path
    if candidate.is_symlink():
        raise LoraDeltaAnalysisError(
            "adapter_file_identity_changed",
            "admitted adapter file became a symbolic link",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise LoraDeltaAnalysisError(
            "adapter_file_identity_changed",
            "admitted adapter file is unavailable",
        ) from exc
    if (
        not resolved.is_relative_to(root)
        or not resolved.is_file()
        or resolved.stat().st_nlink != 1
        or resolved.stat().st_size != entry.size_bytes
    ):
        raise LoraDeltaAnalysisError(
            "adapter_file_identity_changed",
            "admitted adapter file identity changed",
        )
    return resolved


def _read_and_verify(path: Path, entry: AnalysisSnapshotFile) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise LoraDeltaAnalysisError(
            "adapter_file_unreadable",
            "admitted adapter file cannot be read",
        ) from exc
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), entry.sha256):
        raise LoraDeltaAnalysisError(
            "adapter_file_identity_changed",
            "admitted adapter file digest changed",
        )
    return content


def _verify_file_digest(path: Path, entry: AnalysisSnapshotFile) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LoraDeltaAnalysisError(
            "adapter_file_unreadable",
            "admitted adapter file cannot be read",
        ) from exc
    if not hmac.compare_digest(digest.hexdigest(), entry.sha256):
        raise LoraDeltaAnalysisError(
            "adapter_file_identity_changed",
            "admitted adapter file digest changed",
        )


def _positive_integer(value: Any, reason_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LoraDeltaAnalysisError(reason_code, "adapter integer must be positive")
    return value


def _positive_number(value: Any, reason_code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise LoraDeltaAnalysisError(reason_code, "adapter number must be positive")
    return float(value)


def _require_text(value: str, reason_code: str, *, maximum: int) -> None:
    normalized = str(value).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 for character in normalized)
    ):
        raise LoraDeltaAnalysisError(reason_code, "identity text is invalid")


def _require_sha256(value: str, reason_code: str) -> None:
    if _SHA256.fullmatch(str(value)) is None:
        raise LoraDeltaAnalysisError(reason_code, "identity digest is invalid")


def _stable_float(value: float) -> float:
    if not math.isfinite(value):
        raise LoraDeltaAnalysisError(
            "delta_metric_non_finite",
            "delta metric is not finite",
        )
    return float(f"{value:.12g}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


__all__ = [
    "ImmutableAdapterIdentity",
    "ImmutableBaseModelIdentity",
    "LoraAdapterPayload",
    "LoraAdapterReaderPort",
    "LoraDeltaAnalysis",
    "LoraDeltaAnalysisError",
    "LoraDeltaAnalysisPolicy",
    "LoraDeltaAnalyzer",
    "LoraModuleDeltaMetrics",
    "LoraNumericTensor",
    "SafetensorsLoraAdapterReader",
    "SafetensorsLoraReaderPolicy",
]
