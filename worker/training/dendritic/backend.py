"""Closed backend registry and deterministic no-GPU control-plane backend."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dendritic_memory import DendriticJobSpecV1, DendriticMemoryPackManifestV1, canonical_digest


class MockDendriticExperimentBackend:
    def prepare(self, spec: DendriticJobSpecV1) -> Mapping[str, Any]:
        return {"ready": True, "spec_digest": spec.digest, "model_download_performed": False}

    def train(self, spec: DendriticJobSpecV1, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if not 3 <= len(records) <= 100_000:
            raise ValueError("dendritic_dataset_size_invalid")
        record_digest = canonical_digest(list(records))
        weights = b"ANANTA_DENDRITIC_MOCK_V1\0" + bytes.fromhex(canonical_digest([spec.digest, record_digest]))
        parameter_count = spec.configuration.branch_count * spec.configuration.hidden_dimension * 2
        manifest = DendriticMemoryPackManifestV1(
            tenant_id=spec.tenant_id,
            pack_id=f"mock-{spec.spec_id}",
            base_model_id=spec.base_model_id,
            base_model_snapshot_digest=spec.base_model_snapshot_digest,
            architecture_version="branch-projection-v1",
            target_layers=spec.configuration.target_layers,
            parameter_count=parameter_count,
            trainable_parameter_count=parameter_count,
            dataset_manifest_digest=spec.dataset_manifest_digest,
            split_digests={"train": "1" * 64, "validation": "2" * 64, "test": "3" * 64},
            configuration_digest=canonical_digest(spec.configuration.to_dict()),
            metrics_digest=canonical_digest({"mock": True, "records": len(records)}),
            files=[
                {
                    "name": "weights.safetensors",
                    "sha256": hashlib.sha256(weights).hexdigest(),
                    "size_bytes": len(weights),
                    "media_type": "application/vnd.safetensors",
                }
            ],
            executable=False,
        )
        return {
            "manifest": manifest.to_dict(),
            "files": {"weights.safetensors": weights},
            "mock_artifact": True,
            "events": [
                {"sequence": 1, "type": "phase", "phase": "prepare"},
                {"sequence": 2, "type": "checkpoint", "step": 1, "sha256": hashlib.sha256(weights).hexdigest()},
                {"sequence": 3, "type": "completed", "progress_percent": 100},
            ],
        }

    def evaluate(self, spec: DendriticJobSpecV1, pack: DendriticMemoryPackManifestV1) -> Mapping[str, Any]:
        return {"spec_digest": spec.digest, "pack_digest": pack.digest, "mock": True}

    def compose(
        self, spec: DendriticJobSpecV1, parents: Sequence[DendriticMemoryPackManifestV1]
    ) -> Mapping[str, Any]:
        return {"spec_digest": spec.digest, "parent_digests": [item.digest for item in parents], "mock": True}

    def cancel(self, *, run_id: str, attempt_id: str) -> None:
        del run_id, attempt_id


class DendriticBackendRegistry:
    def __init__(self, backends: Mapping[str, Any] | None = None) -> None:
        self._backends = dict(backends or {"mock": MockDendriticExperimentBackend()})
        if set(self._backends) - {"mock", "torch_reference"}:
            raise ValueError("dendritic_backend_registry_invalid")

    def get(self, backend_id: str):
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise ValueError("dendritic_backend_denied") from exc


__all__ = ["DendriticBackendRegistry", "MockDendriticExperimentBackend"]
