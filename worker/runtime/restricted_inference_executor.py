"""Lazy local adapter execution for the isolated restricted-inference worker."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from agent.services.model_inference_adapter_registry import ModelInferenceAdapterRegistry
from agent.services.model_inference_adapters import (
    ChoiceScore,
    ClassificationResult,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)
from agent.services.restricted_inference_cache import (
    RestrictedInferenceCache,
    RestrictedInferenceCacheKey,
)
from agent.services.restricted_inference_config_service import KNOWN_ENGINES, RestrictedInferenceModelConfig
from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
)
from agent.services.restricted_inference_model_manifest import (
    ENGINE_ONNX,
    ENGINE_SENTENCE_TRANSFORMERS,
    ROLE_EXTERNAL_DATA,
    ROLE_WEIGHTS,
    VerifiedModelSnapshot,
)
from worker.runtime.restricted_inference_registry import LazyModelRegistry, ModelLifecycleError
from worker.runtime.restricted_inference_resources import (
    ResourceLeaseManager,
    RestrictedInferenceResourceError,
)


class RestrictedInferenceExecutionError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True)
class WorkerModelPolicy:
    """Immutable container boundary for engines and accelerator families.

    Manifests remain the source of model requirements, while this policy is
    the deployment-side upper bound.  A model can only load when both agree.
    """

    enabled_engines: frozenset[str]
    device_family: str | None = None

    def __post_init__(self) -> None:
        unknown = self.enabled_engines - KNOWN_ENGINES
        if unknown:
            raise ValueError(f"restricted inference worker has unknown engines: {sorted(unknown)}")
        if self.device_family not in {None, "cpu", "cuda"}:
            raise ValueError("restricted inference worker device must be cpu or cuda")

    @classmethod
    def unrestricted_for_tests(cls) -> WorkerModelPolicy:
        return cls(enabled_engines=frozenset(KNOWN_ENGINES), device_family=None)

    def validate(self, snapshot: VerifiedModelSnapshot) -> None:
        manifest = snapshot.manifest
        if manifest is None:
            raise RestrictedInferenceExecutionError(
                "manifest_metadata_missing",
                "snapshot manifest is unavailable",
            )
        if manifest.engine not in self.enabled_engines:
            raise RestrictedInferenceExecutionError(
                "engine_not_enabled",
                "model engine is disabled by the worker deployment policy",
            )
        manifest_family = "cuda" if manifest.device.startswith("cuda") else manifest.device
        if self.device_family is not None and manifest_family != self.device_family:
            raise RestrictedInferenceExecutionError(
                "device_not_enabled",
                "model device is disabled by the worker deployment policy",
            )


class ManifestAdapterFactory:
    """Construct adapters solely from an already verified local snapshot."""

    _ALLOWED_METADATA_OPTIONS = frozenset(
        {
            "labels",
            "normalize_embeddings",
            "pooling",
            "sentence_mode",
            "task",
        }
    )

    def __init__(self, registry: ModelInferenceAdapterRegistry | None = None) -> None:
        self._registry = registry or ModelInferenceAdapterRegistry()

    def __call__(self, snapshot: VerifiedModelSnapshot, *, device: str) -> Any:
        manifest = snapshot.manifest
        if manifest is None:
            raise RestrictedInferenceExecutionError("manifest_metadata_missing", "snapshot manifest is unavailable")
        metadata = {key: value for key, value in manifest.metadata.items() if key in self._ALLOWED_METADATA_OPTIONS}
        metadata.update(
            {
                "allow_attention": False,
                "allow_hidden_states": False,
                "dtype": manifest.dtype,
                "local_files_only": True,
                "max_seq_length": manifest.max_sequence_length,
                "quantization": manifest.quantization,
                "trust_remote_code": False,
            }
        )
        metadata["tokenizer_path"] = str(
            snapshot.root / Path(manifest.tokenizer).parent if manifest.tokenizer else snapshot.root
        )
        operation_set = set(manifest.operations)
        if RestrictedInferenceOperation.SCORE_CHOICES in operation_set:
            metadata["task"] = "causal-choice-scoring"
        elif operation_set & {
            RestrictedInferenceOperation.CLASSIFY,
            RestrictedInferenceOperation.RISK_SCORE,
        }:
            metadata.setdefault("task", "sequence-classification")
        else:
            metadata.setdefault("task", "feature-extraction")
        local_path = str(snapshot.root)
        model_path = local_path
        if manifest.engine == ENGINE_SENTENCE_TRANSFORMERS:
            metadata["sentence_mode"] = (
                "cross_encoder" if manifest.operations == (RestrictedInferenceOperation.RERANK,) else "bi_encoder"
            )
        if manifest.engine == ENGINE_ONNX:
            weight_files = [item.relative_path for item in manifest.files if item.role == ROLE_WEIGHTS]
            if len(weight_files) != 1:
                raise RestrictedInferenceExecutionError(
                    "invalid_onnx_snapshot",
                    "ONNX snapshots require exactly one weights file",
                )
            model_path = str(snapshot.root / weight_files[0])
            weight_parent = Path(weight_files[0]).parent
            metadata["allowed_external_data"] = [
                Path(item.relative_path).relative_to(weight_parent).as_posix()
                for item in manifest.files
                if item.role == ROLE_EXTERNAL_DATA and Path(item.relative_path).is_relative_to(weight_parent)
            ]
        config = RestrictedInferenceModelConfig(
            id=manifest.model_id,
            engine=manifest.engine,
            model=model_path,
            revision=manifest.revision,
            local_path=model_path,
            device=device,
            enabled=True,
            tasks=[operation.value for operation in manifest.operations],
            options=metadata,
        )
        return self._registry.build(config)


class LazyAdapterExecutor:
    """Execute fixed operations under queue, model, and resource leases."""

    def __init__(
        self,
        *,
        registry: LazyModelRegistry,
        resources: ResourceLeaseManager,
        cache: RestrictedInferenceCache | None = None,
        allow_cpu_fallback: bool = False,
        max_output_dimensions: int = 65_536,
        model_policy: WorkerModelPolicy | None = None,
    ) -> None:
        if max_output_dimensions < 1:
            raise ValueError("max_output_dimensions must be positive")
        self._registry = registry
        self._resources = resources
        self._cache = cache if cache is not None else RestrictedInferenceCache(max_entries=0)
        self._allow_cpu_fallback = allow_cpu_fallback
        self._max_output_dimensions = max_output_dimensions
        self._model_policy = model_policy or WorkerModelPolicy.unrestricted_for_tests()
        self._configuration_lock = threading.Lock()
        self._configuration_version = 1

    def execute(
        self,
        request: RestrictedInferenceRequest,
        snapshot: VerifiedModelSnapshot,
    ) -> Mapping[str, Any]:
        manifest = snapshot.manifest
        if manifest is None:
            raise RestrictedInferenceExecutionError("manifest_metadata_missing", "snapshot manifest is unavailable")
        self._model_policy.validate(snapshot)
        if request.operation not in manifest.operations:
            raise RestrictedInferenceExecutionError(
                "operation_not_allowed",
                "operation is not declared by the model manifest",
            )
        requested_device = str(request.execution_policy["device"])
        if requested_device and requested_device != manifest.device:
            raise RestrictedInferenceExecutionError(
                "device_policy_mismatch",
                "request device does not match the admitted manifest",
            )
        if request.execution_policy["allow_hidden_states"] or request.execution_policy["allow_attention"]:
            raise RestrictedInferenceExecutionError(
                "unsupported_sensitive_output",
                "hidden-state and attention output are not exposed by this contract",
            )
        self._validate_manifest_limits(request, manifest.max_batch_size, manifest.max_sequence_length)
        cache_key = RestrictedInferenceCacheKey.build(
            tenant_id=request.tenant_id,
            operation=request.operation.value,
            manifest_digest=snapshot.manifest_digest,
            policy_hash=request.policy_hash,
            config={
                "device": manifest.device,
                "dtype": manifest.dtype,
                "quantization": manifest.quantization,
                "max_sequence_length": manifest.max_sequence_length,
            },
            payload=request.to_dict()["payload"],
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cast(Mapping[str, Any], cached)
        with self._configuration_lock:
            worker_allows_cpu_fallback = self._allow_cpu_fallback
        try:
            with self._resources.execution(deadline_epoch_ms=request.deadline_epoch_ms):
                with self._registry.lease(
                    snapshot,
                    deadline_epoch_ms=request.deadline_epoch_ms,
                    allow_cpu_fallback=(
                        worker_allows_cpu_fallback and bool(request.execution_policy["allow_cpu_fallback"])
                    ),
                ) as adapter:
                    result = self._dispatch(adapter, request)
        except (ModelLifecycleError, RestrictedInferenceResourceError) as exc:
            raise RestrictedInferenceExecutionError(
                exc.reason_code,
                "restricted inference resource admission failed",
                retryable=getattr(exc, "retryable", True),
            ) from exc
        except MemoryError as exc:
            raise RestrictedInferenceExecutionError(
                "out_of_memory", "model execution exhausted memory", retryable=True
            ) from exc
        except Exception as exc:
            if _is_out_of_memory(exc):
                raise RestrictedInferenceExecutionError(
                    "out_of_memory",
                    "model execution exhausted memory",
                    retryable=True,
                ) from exc
            raise
        self._validate_numeric_bounds(
            result,
            max_dimensions=min(
                self._max_output_dimensions,
                int(request.execution_policy["max_output_dimensions"]),
            ),
        )
        self._cache.put(cache_key, result)
        return result

    def status(self) -> dict[str, Any]:
        return {
            "models": [status.to_dict() for status in self._registry.statuses()],
            "resources": self._resources.snapshot(),
            "cache_entries": len(self._cache),
        }

    def unload(self, manifest_digest: str) -> bool:
        return bool(self._registry.evict(manifest_digest))

    def load(self, snapshot: VerifiedModelSnapshot, *, deadline_epoch_ms: int) -> dict[str, Any]:
        with self._configuration_lock:
            allow_cpu_fallback = self._allow_cpu_fallback
        status = self._registry.preload(
            snapshot,
            deadline_epoch_ms=deadline_epoch_ms,
            allow_cpu_fallback=allow_cpu_fallback,
        )
        return dict(status.to_dict())

    def configuration(self) -> dict[str, Any]:
        with self._configuration_lock:
            return {
                "schema_version": "ananta.restricted-runtime-config.v1",
                "version": self._configuration_version,
                "mutable": {"allow_cpu_fallback": self._allow_cpu_fallback},
                "fixed": {
                    "device_family": self._model_policy.device_family or "manifest",
                    "downloads_allowed": False,
                    "enabled_engines": sorted(self._model_policy.enabled_engines),
                    "generation_allowed": False,
                    "local_snapshots_only": True,
                    "trust_remote_code": False,
                },
            }

    def update_configuration(self, delta: Mapping[str, Any], *, expected_version: int) -> dict[str, Any]:
        if set(delta) - {"allow_cpu_fallback"} or not isinstance(
            delta.get("allow_cpu_fallback"),
            bool,
        ):
            raise RestrictedInferenceExecutionError(
                "invalid_runtime_configuration",
                "runtime configuration delta is invalid",
            )
        with self._configuration_lock:
            if expected_version != self._configuration_version:
                raise RestrictedInferenceExecutionError(
                    "configuration_version_conflict",
                    "runtime configuration version does not match",
                    retryable=True,
                )
            changed = self._allow_cpu_fallback != delta["allow_cpu_fallback"]
            if changed:
                self._allow_cpu_fallback = bool(delta["allow_cpu_fallback"])
                self._configuration_version += 1
            return {
                "schema_version": "ananta.restricted-runtime-config.v1",
                "version": self._configuration_version,
                "mutable": {"allow_cpu_fallback": self._allow_cpu_fallback},
                "fixed": {
                    "device_family": self._model_policy.device_family or "manifest",
                    "downloads_allowed": False,
                    "enabled_engines": sorted(self._model_policy.enabled_engines),
                    "generation_allowed": False,
                    "local_snapshots_only": True,
                    "trust_remote_code": False,
                },
                "changed": changed,
            }

    def cache_gc(self) -> int:
        return int(self._cache.clear())

    @staticmethod
    def _dispatch(adapter: Any, request: RestrictedInferenceRequest) -> dict[str, Any]:
        payload = request.payload
        operation = request.operation
        if operation is RestrictedInferenceOperation.EMBED:
            return {"vectors": adapter.embed(list(payload["texts"]))}
        if operation is RestrictedInferenceOperation.CLASSIFY:
            result: ClassificationResult = adapter.classify(str(payload["text"]), list(payload["labels"]))
            return {
                "label": result.label,
                "confidence": result.confidence,
                "all_scores": dict(result.all_scores),
            }
        if operation is RestrictedInferenceOperation.RERANK:
            items: list[RerankResult] = adapter.rerank(
                str(payload["query"]), [dict(item) for item in payload["candidates"]]
            )
            return {
                "items": [
                    {
                        "path": item.path,
                        "record_id": item.record_id,
                        "score": item.score,
                        "confidence": item.confidence,
                        "reason_code": item.reason_code,
                    }
                    for item in items
                ]
            }
        if operation is RestrictedInferenceOperation.SCORE_CHOICES:
            scores: list[ChoiceScore] = adapter.score_choices(str(payload["prompt"]), list(payload["choices"]))
            return {"items": [{"choice": item.choice, "score": item.score} for item in scores]}
        if operation is RestrictedInferenceOperation.EXTRACT_FEATURES:
            features: FeatureVector = adapter.extract_features(str(payload["text"]))
            return {"vector": list(features.vector), "dimensions": features.dimensions}
        if operation is RestrictedInferenceOperation.RISK_SCORE:
            risk: RiskScoreResult = adapter.risk_score(dict(payload["input"]))
            return {
                "risk_score": risk.risk_score,
                "risk_category": risk.risk_category,
                "confidence": risk.confidence,
            }
        raise RestrictedInferenceExecutionError("unknown_operation", "operation is not supported")

    @staticmethod
    def _validate_manifest_limits(request: RestrictedInferenceRequest, max_batch: int, max_sequence: int) -> None:
        payload = request.payload
        if request.operation is RestrictedInferenceOperation.EMBED:
            batch = len(payload["texts"])
            texts = payload["texts"]
        elif request.operation is RestrictedInferenceOperation.RERANK:
            batch = len(payload["candidates"])
            texts = [
                payload["query"],
                *(str(item.get("excerpt") or item.get("path") or "") for item in payload["candidates"]),
            ]
        elif request.operation is RestrictedInferenceOperation.SCORE_CHOICES:
            batch = len(payload["choices"])
            texts = [payload["prompt"], *payload["choices"]]
        elif request.operation is RestrictedInferenceOperation.RISK_SCORE:
            batch = 1
            texts = [" ".join(str(value) for value in payload["input"].values())]
        else:
            batch = 1
            texts = [payload["text"]]
        if batch > max_batch:
            raise RestrictedInferenceExecutionError("batch_limit_exceeded", "request exceeds manifest batch limit")
        # Character count is a conservative pre-tokenization guard. The adapter
        # separately applies the manifest-bound tokenizer sequence limit.
        if any(len(str(text)) > max_sequence * 16 for text in texts):
            raise RestrictedInferenceExecutionError("sequence_limit_exceeded", "request exceeds sequence limit")

    def _validate_numeric_bounds(self, result: Mapping[str, Any], *, max_dimensions: int) -> None:
        values: list[Any] = []
        if "vectors" in result:
            vectors = result["vectors"]
            if any(len(vector) > max_dimensions for vector in vectors):
                raise RestrictedInferenceExecutionError("output_limit_exceeded", "embedding dimension limit exceeded")
            values.extend(number for vector in vectors for number in vector)
        if "vector" in result:
            if len(result["vector"]) > max_dimensions:
                raise RestrictedInferenceExecutionError("output_limit_exceeded", "feature dimension limit exceeded")
            values.extend(result["vector"])
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise RestrictedInferenceExecutionError("non_finite_result", "model returned a non-finite number")


def build_default_executor(
    *,
    resources: ResourceLeaseManager,
    cache_entries: int = 0,
    cache_ttl_seconds: float = 300.0,
    allow_cpu_fallback: bool = False,
    enabled_engines: Iterable[str] | None = None,
    worker_device: str | None = None,
) -> tuple[LazyAdapterExecutor, LazyModelRegistry]:
    normalized_engines = (
        frozenset(str(engine).strip().lower() for engine in enabled_engines if str(engine).strip())
        if enabled_engines is not None
        else frozenset(KNOWN_ENGINES)
    )
    normalized_device = str(worker_device).strip().lower() if worker_device is not None else None
    model_policy = WorkerModelPolicy(
        enabled_engines=normalized_engines,
        device_family=normalized_device or None,
    )
    registry = LazyModelRegistry(adapter_factory=ManifestAdapterFactory(), resources=resources)
    executor = LazyAdapterExecutor(
        registry=registry,
        resources=resources,
        cache=RestrictedInferenceCache(max_entries=cache_entries, ttl_seconds=cache_ttl_seconds),
        allow_cpu_fallback=allow_cpu_fallback,
        model_policy=model_policy,
    )
    return executor, registry


def _is_out_of_memory(exc: Exception) -> bool:
    return "outofmemory" in type(exc).__name__.lower() or "out of memory" in str(exc).lower()
