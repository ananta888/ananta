from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from agent.services.model_inference_adapters import AdapterStatus, ClassificationResult
from agent.services.restricted_inference_cache import RestrictedInferenceCache
from agent.services.restricted_inference_contract import RestrictedInferenceOperation, RestrictedInferenceRequest
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    RestrictedModelManifest,
    VerifiedModelSnapshot,
)
from worker.runtime.restricted_inference_executor import (
    LazyAdapterExecutor,
    RestrictedInferenceExecutionError,
    WorkerModelPolicy,
)
from worker.runtime.restricted_inference_registry import LazyModelRegistry
from worker.runtime.restricted_inference_resources import (
    DeviceAvailability,
    ResourceBudget,
    ResourceLeaseManager,
)


class _Probe:
    def available(self, device: str) -> DeviceAvailability:
        return DeviceAvailability(10_000, 10_000 if device.startswith("cuda") else None)


class _Classifier:
    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> AdapterStatus:
        return AdapterStatus("fixture", ENGINE_HUGGINGFACE, "ready")

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        self.calls += 1
        return ClassificationResult(labels[0], 1.0, {labels[0]: 1.0}, "fixture/model", ENGINE_HUGGINGFACE)


def _snapshot(
    tmp_path: Path,
    *,
    engine: str = ENGINE_HUGGINGFACE,
    device: str = "cpu",
) -> VerifiedModelSnapshot:
    digest = hashlib.sha256(b"").hexdigest()
    manifest = RestrictedModelManifest(
        manifest_id="manifest-1",
        model_id="fixture/model",
        engine=engine,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=(ModelManifestFile("model.safetensors", digest, 0, ROLE_WEIGHTS),),
        ram_bytes=100,
        max_batch_size=2,
        max_sequence_length=16,
        device=device,
    )
    return VerifiedModelSnapshot(
        root=tmp_path,
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.digest,
        model_id=manifest.model_id,
        engine=manifest.engine,
        total_size_bytes=0,
        file_digests={"model.safetensors": digest},
        manifest=manifest,
    )


def _request(*, tenant: str = "tenant-a", operation=RestrictedInferenceOperation.CLASSIFY, policy=None):
    payload = {"text": "safe", "labels": ["safe"]}
    if operation is RestrictedInferenceOperation.EXTRACT_FEATURES:
        payload = {"text": "safe"}
    return RestrictedInferenceRequest(
        request_id=f"request-{tenant}-{operation.value}",
        task_id="task-1",
        run_id="run-1",
        tenant_id=tenant,
        operation=operation,
        payload=payload,
        model_manifest_id="manifest-1",
        policy_hash="policy-1",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
        execution_policy=policy or {},
    )


def _executor(
    adapter: _Classifier,
    *,
    model_policy: WorkerModelPolicy | None = None,
) -> LazyAdapterExecutor:
    resources = ResourceLeaseManager(
        ResourceBudget(max_ram_bytes=10_000, max_loaded_models=2, max_in_flight=2, max_queue=2),
        probe=_Probe(),
    )
    registry = LazyModelRegistry(adapter_factory=lambda _snapshot, device: adapter, resources=resources)
    return LazyAdapterExecutor(
        registry=registry,
        resources=resources,
        cache=RestrictedInferenceCache(max_entries=8, ttl_seconds=60),
        model_policy=model_policy,
    )


def test_cache_is_tenant_policy_and_manifest_bound(tmp_path: Path) -> None:
    adapter = _Classifier()
    executor = _executor(adapter)
    snapshot = _snapshot(tmp_path)

    first = executor.execute(_request(tenant="tenant-a"), snapshot)
    second = executor.execute(_request(tenant="tenant-a"), snapshot)
    other_tenant = executor.execute(_request(tenant="tenant-b"), snapshot)

    assert first == second == other_tenant
    assert adapter.calls == 2
    assert executor.status()["cache_entries"] == 2


def test_executor_rejects_operation_not_declared_by_manifest(tmp_path: Path) -> None:
    executor = _executor(_Classifier())

    with pytest.raises(RestrictedInferenceExecutionError) as error:
        executor.execute(_request(operation=RestrictedInferenceOperation.EXTRACT_FEATURES), _snapshot(tmp_path))

    assert error.value.reason_code == "operation_not_allowed"


def test_executor_rejects_sensitive_outputs_even_when_envelope_requests_them(tmp_path: Path) -> None:
    executor = _executor(_Classifier())

    with pytest.raises(RestrictedInferenceExecutionError) as error:
        executor.execute(_request(policy={"allow_hidden_states": True}), _snapshot(tmp_path))

    assert error.value.reason_code == "unsupported_sensitive_output"


def test_worker_policy_rejects_disabled_engine_before_adapter_load(tmp_path: Path) -> None:
    adapter = _Classifier()
    executor = _executor(
        adapter,
        model_policy=WorkerModelPolicy(
            enabled_engines=frozenset({"onnxruntime"}),
            device_family="cpu",
        ),
    )

    with pytest.raises(RestrictedInferenceExecutionError) as error:
        executor.execute(_request(), _snapshot(tmp_path))

    assert error.value.reason_code == "engine_not_enabled"
    assert adapter.calls == 0


def test_worker_policy_rejects_manifest_for_other_device_family(tmp_path: Path) -> None:
    adapter = _Classifier()
    executor = _executor(
        adapter,
        model_policy=WorkerModelPolicy(
            enabled_engines=frozenset({ENGINE_HUGGINGFACE}),
            device_family="cpu",
        ),
    )

    with pytest.raises(RestrictedInferenceExecutionError) as error:
        executor.execute(_request(policy={"device": "cuda:0"}), _snapshot(tmp_path, device="cuda:0"))

    assert error.value.reason_code == "device_not_enabled"
    assert adapter.calls == 0


def test_framework_out_of_memory_is_typed_and_model_is_failed(tmp_path: Path) -> None:
    class _OomClassifier(_Classifier):
        def classify(self, text: str, labels: list[str]) -> ClassificationResult:
            raise RuntimeError("CUDA out of memory")

    executor = _executor(_OomClassifier())

    with pytest.raises(RestrictedInferenceExecutionError) as error:
        executor.execute(_request(), _snapshot(tmp_path))

    assert error.value.reason_code == "out_of_memory"
    assert error.value.retryable is True
    assert executor.status()["resources"]["loaded_models"] == 0
