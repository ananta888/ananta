from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest

from agent.services.model_inference_adapters import AdapterStatus
from agent.services.restricted_inference_contract import RestrictedInferenceOperation
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    RestrictedModelManifest,
    VerifiedModelSnapshot,
)
from worker.runtime.restricted_inference_registry import (
    LazyModelRegistry,
    ModelLifecycleError,
    ModelLifecycleState,
)
from worker.runtime.restricted_inference_resources import (
    DeviceAvailability,
    ResourceBudget,
    ResourceLeaseManager,
    RestrictedInferenceResourceError,
)


class _Adapter:
    def __init__(self) -> None:
        self.closed = False

    def status(self) -> AdapterStatus:
        return AdapterStatus("fixture", ENGINE_HUGGINGFACE, "ready")

    def close(self) -> None:
        self.closed = True


class _Probe:
    def __init__(self, *, ram: int = 10_000, vram: int = 10_000) -> None:
        self.ram = ram
        self.vram = vram

    def available(self, device: str) -> DeviceAvailability:
        return DeviceAvailability(self.ram, self.vram if device.startswith("cuda") else None)


def _snapshot(tmp_path: Path, *, device: str = "cpu", allow_cpu_fallback: bool = False) -> VerifiedModelSnapshot:
    manifest = RestrictedModelManifest(
        manifest_id=f"fixture-{device.replace(':', '-')}",
        model_id="fixture/model",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=(ModelManifestFile("model.safetensors", hashlib.sha256(b"").hexdigest(), 0, ROLE_WEIGHTS),),
        device=device,
        ram_bytes=100,
        vram_bytes=100 if device.startswith("cuda") else 0,
        allow_cpu_fallback=allow_cpu_fallback,
    )
    return VerifiedModelSnapshot(
        root=tmp_path,
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.digest,
        model_id=manifest.model_id,
        engine=manifest.engine,
        total_size_bytes=0,
        file_digests={"model.safetensors": hashlib.sha256(b"").hexdigest()},
        manifest=manifest,
    )


def _resources(*, max_models: int = 2, max_in_flight: int = 4, max_queue: int = 4, probe=None):
    return ResourceLeaseManager(
        ResourceBudget(
            max_ram_bytes=10_000,
            max_vram_bytes=10_000,
            max_loaded_models=max_models,
            max_in_flight=max_in_flight,
            max_queue=max_queue,
        ),
        probe=probe or _Probe(),
    )


def test_parallel_loads_initialize_digest_exactly_once(tmp_path: Path) -> None:
    calls = 0
    calls_lock = Lock()

    def factory(_snapshot: VerifiedModelSnapshot, *, device: str) -> _Adapter:
        nonlocal calls
        assert device == "cpu"
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return _Adapter()

    snapshot = _snapshot(tmp_path)
    registry = LazyModelRegistry(adapter_factory=factory, resources=_resources())
    deadline = time.time_ns() // 1_000_000 + 5_000

    def use_model(_index: int) -> int:
        with registry.lease(snapshot, deadline_epoch_ms=deadline) as adapter:
            assert isinstance(adapter, _Adapter)
            return id(adapter)

    with ThreadPoolExecutor(max_workers=8) as pool:
        adapter_ids = list(pool.map(use_model, range(8)))

    assert calls == 1
    assert len(set(adapter_ids)) == 1
    assert registry.statuses()[0].state is ModelLifecycleState.IDLE


def test_active_lease_blocks_unload_then_idle_model_is_evicted(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    registry = LazyModelRegistry(adapter_factory=lambda _snapshot, device: _Adapter(), resources=_resources())
    deadline = time.time_ns() // 1_000_000 + 5_000

    with registry.lease(snapshot, deadline_epoch_ms=deadline):
        with pytest.raises(ModelLifecycleError) as error:
            registry.evict(snapshot.manifest_digest)
        assert error.value.reason_code == "model_in_use"

    assert registry.evict(snapshot.manifest_digest) is True
    assert registry.statuses()[0].state is ModelLifecycleState.EVICTED


def test_explicit_preload_uses_verified_registry_and_is_idempotently_idle(tmp_path: Path) -> None:
    calls = 0

    def factory(_snapshot: VerifiedModelSnapshot, *, device: str) -> _Adapter:
        nonlocal calls
        calls += 1
        assert device == "cpu"
        return _Adapter()

    snapshot = _snapshot(tmp_path)
    registry = LazyModelRegistry(adapter_factory=factory, resources=_resources())
    deadline = time.time_ns() // 1_000_000 + 5_000

    first = registry.preload(snapshot, deadline_epoch_ms=deadline)
    replay = registry.preload(snapshot, deadline_epoch_ms=deadline)

    assert calls == 1
    assert first.manifest_digest == snapshot.manifest_digest
    assert first.state is ModelLifecycleState.IDLE
    assert replay.state is ModelLifecycleState.IDLE
    assert replay.active_leases == 0


def test_failed_load_releases_resources_and_can_recover(tmp_path: Path) -> None:
    calls = 0

    def factory(_snapshot: VerifiedModelSnapshot, *, device: str) -> _Adapter:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MemoryError("fixture")
        return _Adapter()

    resources = _resources()
    snapshot = _snapshot(tmp_path)
    registry = LazyModelRegistry(
        adapter_factory=factory,
        resources=resources,
        failure_retry_seconds=0,
    )
    deadline = time.time_ns() // 1_000_000 + 5_000

    with pytest.raises(ModelLifecycleError) as error:
        with registry.lease(snapshot, deadline_epoch_ms=deadline):
            pass
    assert error.value.reason_code == "out_of_memory"
    assert resources.snapshot()["loaded_models"] == 0

    with registry.lease(snapshot, deadline_epoch_ms=deadline) as adapter:
        assert isinstance(adapter, _Adapter)
    assert calls == 2


def test_cpu_fallback_requires_worker_manifest_and_request_consent(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, device="cuda:0", allow_cpu_fallback=True)
    devices: list[str] = []

    def factory(_snapshot: VerifiedModelSnapshot, *, device: str) -> _Adapter:
        devices.append(device)
        return _Adapter()

    resources = _resources(probe=_Probe(vram=0))
    registry = LazyModelRegistry(adapter_factory=factory, resources=resources)
    deadline = time.time_ns() // 1_000_000 + 5_000

    with registry.lease(snapshot, deadline_epoch_ms=deadline, allow_cpu_fallback=True):
        pass

    assert devices == ["cpu"]
    assert registry.statuses()[0].loaded_device == "cpu"


def test_request_capacity_is_bounded_without_hidden_waiters() -> None:
    resources = _resources(max_in_flight=1, max_queue=0)
    deadline = time.time_ns() // 1_000_000 + 5_000

    with resources.execution(deadline_epoch_ms=deadline):
        with pytest.raises(RestrictedInferenceResourceError) as error:
            with resources.execution(deadline_epoch_ms=deadline):
                pass
    assert error.value.reason_code == "queue_full"
    assert resources.snapshot()["active"] == 0
