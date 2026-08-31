from __future__ import annotations

from dataclasses import replace

import pytest

from ananta_contracts.dendritic_memory import DendriticMemoryPackManifestV1
from tests.dendritic_memory.helpers import pack
from worker.runtime.dendritic_memory_loader import DendriticMemoryRuntimeLoader


class _Loaded:
    def __init__(self) -> None:
        self.unloaded = False

    def unload(self) -> None:
        self.unloaded = True


def _runtime(tmp_path, manifest: DendriticMemoryPackManifestV1, weights: bytes):
    root = tmp_path / "packs"
    location = root / manifest.tenant_id / manifest.digest
    location.mkdir(parents=True)
    (location / "weights.safetensors").write_bytes(weights)
    loaded: list[_Loaded] = []

    def load(_manifest, _weights):
        value = _Loaded()
        loaded.append(value)
        return value

    return DendriticMemoryRuntimeLoader(
        artifact_root=root,
        model_catalog={"mock-local-model": {"snapshot_digest": "b" * 64}},
        load_module=load,
        max_pack_bytes=1_048_576,
    ), loaded


def test_runtime_loads_only_exact_approved_pack_and_unloads_without_human(tmp_path) -> None:
    manifest, files = pack(executable=True)
    runtime, loaded = _runtime(tmp_path, manifest, files["weights.safetensors"])
    active = runtime.activate(
        scope_id="planning",
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        manifest=manifest.to_dict(),
        registry_state="approved_for_experiment",
    )
    assert active["active"] is True
    assert active["human_intervention_required"] is False
    inactive = runtime.deactivate(scope_id="planning")
    assert inactive["fallback"] == "unchanged_base_model"
    assert loaded[0].unloaded is True


def test_runtime_rejects_hash_mismatch_before_loading(tmp_path) -> None:
    manifest, files = pack(executable=True)
    changed = replace(manifest, base_model_snapshot_digest="c" * 64)
    runtime, loaded = _runtime(tmp_path, changed, files["weights.safetensors"])
    with pytest.raises(PermissionError, match="model_binding_invalid"):
        runtime.activate(
            scope_id="planning",
            tenant_id="tenant-1",
            pack_digest=changed.digest,
            manifest=changed.to_dict(),
            registry_state="approved_for_experiment",
        )
    assert loaded == []
