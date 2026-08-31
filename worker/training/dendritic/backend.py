"""Closed backend registry and deterministic no-GPU control-plane backend."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dendritic_memory import DendriticJobSpecV1, DendriticMemoryPackManifestV1, canonical_digest
from worker.training.dendritic.injection import inject_dendritic_modules
from worker.training.dendritic.module import build_dendritic_memory_module, parameter_report
from worker.training.dendritic.pack_io import DendriticSafetensorsPackIo


class MockDendriticExperimentBackend:
    def __init__(self, *, scenario: str = "success") -> None:
        if scenario not in {"success", "timeout", "corrupt", "retry"}:
            raise ValueError("dendritic_mock_scenario_invalid")
        self._scenario = scenario

    def prepare(self, spec: DendriticJobSpecV1) -> Mapping[str, Any]:
        return {"ready": True, "spec_digest": spec.digest, "model_download_performed": False}

    def train(self, spec: DendriticJobSpecV1, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if not 3 <= len(records) <= 100_000:
            raise ValueError("dendritic_dataset_size_invalid")
        if self._scenario == "timeout":
            raise RuntimeError("dendritic_worker_timeout")
        if self._scenario == "retry":
            raise RuntimeError("dendritic_worker_retryable_failure")
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
        files = {"weights.safetensors": weights}
        if self._scenario == "corrupt":
            files = {"weights.safetensors": b"corrupt"}
        return {
            "manifest": manifest.to_dict(),
            "files": files,
            "mock_artifact": True,
            "events": [
                {"sequence": 1, "type": "phase", "phase": "prepare"},
                {"sequence": 2, "type": "checkpoint", "step": 1, "sha256": hashlib.sha256(weights).hexdigest()},
                {"sequence": 3, "type": "metric", "name": "mock_loss", "value": 0.0},
                {"sequence": 4, "type": "completed", "progress_percent": 100},
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


class TorchReferenceDendriticExperimentBackend:
    """Small falsifiable training backend over an injected read-only model catalog."""

    def __init__(self, model_catalog: Any, *, clock=time.monotonic) -> None:
        self._models = model_catalog
        self._clock = clock

    def prepare(self, spec: DendriticJobSpecV1) -> Mapping[str, Any]:
        resolved = self._models.resolve(
            model_id=spec.base_model_id, snapshot_digest=spec.base_model_snapshot_digest
        )
        return {
            "ready": True,
            "spec_digest": spec.digest,
            "catalog_entry_digest": canonical_digest(
                {key: value for key, value in resolved.items() if key != "model"}
            ),
            "model_download_performed": False,
        }

    def train(self, spec: DendriticJobSpecV1, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("dendritic_torch_unavailable") from exc
        if not 3 <= len(records) <= 100_000:
            raise ValueError("dendritic_dataset_size_invalid")
        resolved = self._models.resolve(
            model_id=spec.base_model_id, snapshot_digest=spec.base_model_snapshot_digest
        )
        model = resolved.get("model")
        allowed_prefixes = tuple(resolved.get("allowed_target_prefixes") or ())
        if model is None or not allowed_prefixes:
            raise ValueError("dendritic_model_catalog_entry_invalid")
        features, targets, split_digests = _materialize_training_records(records, spec.configuration.hidden_dimension)
        torch.manual_seed(spec.configuration.seed)
        modules = {
            target: build_dendritic_memory_module(
                hidden_dimension=spec.configuration.hidden_dimension,
                branch_count=spec.configuration.branch_count,
                top_k=spec.configuration.top_k,
                routing_enabled=spec.configuration.routing_enabled,
                readout=spec.configuration.readout,
                max_memory_bytes=spec.configuration.max_memory_bytes,
            )
            for target in spec.configuration.target_layers
        }
        handle = inject_dendritic_modules(
            model,
            targets=tuple(spec.configuration.target_layers),
            modules=modules,
            allowed_prefixes=allowed_prefixes,
        )
        parameters = [parameter for module in modules.values() for parameter in module.parameters()]
        optimizer = torch.optim.AdamW(parameters, lr=1e-3)
        started = self._clock()
        events: list[dict[str, Any]] = [{"sequence": 1, "type": "phase", "phase": "train"}]
        try:
            for step in range(spec.configuration.max_steps):
                optimizer.zero_grad(set_to_none=True)
                prediction = model(features)
                if isinstance(prediction, tuple):
                    prediction = prediction[0]
                loss = torch.nn.functional.mse_loss(prediction.float(), targets.float())
                if not torch.isfinite(loss):
                    raise RuntimeError("dendritic_training_non_finite")
                loss.backward()
                optimizer.step()
                events.append(
                    {
                        "sequence": len(events) + 1,
                        "type": "metric",
                        "name": "train_loss",
                        "step": step + 1,
                        "value": float(loss.detach().cpu()),
                    }
                )
        except torch.cuda.OutOfMemoryError as exc:
            raise RuntimeError("dendritic_worker_oom") from exc
        finally:
            handle.unpatch()
        state = {
            f"memory.{target}.{key}": value.detach().cpu()
            for target, module in modules.items()
            for key, value in module.state_dict().items()
        }
        weights = DendriticSafetensorsPackIo().dump(state)
        reports = [parameter_report(module) for module in modules.values()]
        parameter_count = sum(value["parameter_count"] for value in reports)
        metrics = {
            "final_loss": events[-1]["value"],
            "duration_ms": max(0, round((self._clock() - started) * 1000, 3)),
            "seed": spec.configuration.seed,
            "deterministic": spec.configuration.deterministic,
        }
        events.append(
            {
                "sequence": len(events) + 1,
                "type": "checkpoint",
                "step": spec.configuration.max_steps,
                "sha256": hashlib.sha256(weights).hexdigest(),
            }
        )
        manifest = DendriticMemoryPackManifestV1(
            tenant_id=spec.tenant_id,
            pack_id=f"reference-{spec.spec_id}",
            base_model_id=spec.base_model_id,
            base_model_snapshot_digest=spec.base_model_snapshot_digest,
            architecture_version="branch-projection-v1",
            target_layers=spec.configuration.target_layers,
            parameter_count=parameter_count,
            trainable_parameter_count=parameter_count,
            dataset_manifest_digest=spec.dataset_manifest_digest,
            split_digests=split_digests,
            configuration_digest=canonical_digest(spec.configuration.to_dict()),
            metrics_digest=canonical_digest(metrics),
            files=(
                {
                    "name": "weights.safetensors",
                    "sha256": hashlib.sha256(weights).hexdigest(),
                    "size_bytes": len(weights),
                    "media_type": "application/vnd.safetensors",
                },
            ),
        )
        return {
            "manifest": manifest.to_dict(),
            "files": {"weights.safetensors": weights},
            "metrics": metrics,
            "events": events,
        }

    def evaluate(self, spec: DendriticJobSpecV1, pack: DendriticMemoryPackManifestV1) -> Mapping[str, Any]:
        return {"spec_digest": spec.digest, "pack_digest": pack.digest, "backend": "torch_reference"}

    def compose(
        self, spec: DendriticJobSpecV1, parents: Sequence[DendriticMemoryPackManifestV1]
    ) -> Mapping[str, Any]:
        return {
            "spec_digest": spec.digest,
            "parent_digests": [item.digest for item in parents],
            "backend": "torch_reference",
        }

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


def _materialize_training_records(records: Sequence[Mapping[str, Any]], hidden: int):
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("dendritic_torch_unavailable") from exc
    allowed = {"features", "target", "split"}
    if any(set(record) != allowed or record["split"] not in {"train", "validation", "test"} for record in records):
        raise ValueError("dendritic_training_record_fields_invalid")
    digests_by_split: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for record in records:
        digest = canonical_digest({"features": record["features"], "target": record["target"]})
        digests_by_split[str(record["split"])].append(digest)
    sets = [set(values) for values in digests_by_split.values()]
    overlaps = any(
        sets[left] & sets[right]
        for left in range(3)
        for right in range(left + 1, 3)
    )
    if any(not values for values in sets) or overlaps:
        raise ValueError("dendritic_dataset_split_leakage")
    train = [record for record in records if record["split"] == "train"]
    features = torch.tensor([record["features"] for record in train], dtype=torch.float32)
    targets = torch.tensor([record["target"] for record in train], dtype=torch.float32)
    if features.ndim != 2 or targets.shape != features.shape or features.shape[1] != hidden:
        raise ValueError("dendritic_training_record_shape_invalid")
    if not torch.isfinite(features).all() or not torch.isfinite(targets).all():
        raise ValueError("dendritic_training_record_non_finite")
    return features, targets, {
        split: canonical_digest(sorted(values)) for split, values in digests_by_split.items()
    }


__all__ = [
    "DendriticBackendRegistry",
    "MockDendriticExperimentBackend",
    "TorchReferenceDendriticExperimentBackend",
]
