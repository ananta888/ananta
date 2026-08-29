from __future__ import annotations

import hashlib
from typing import Any

from ananta_contracts.dendritic_memory import (
    DendriticExperimentConfigV1,
    DendriticJobSpecV1,
    DendriticMemoryPackManifestV1,
    canonical_digest,
)


def config() -> DendriticExperimentConfigV1:
    return DendriticExperimentConfigV1(
        target_layers=("model.layers.0",),
        branch_count=4,
        hidden_dimension=16,
        top_k=2,
        routing_enabled=True,
        readout="gated_residual",
        max_steps=10,
        max_memory_bytes=1_048_576,
        seed=7,
        precision="float32",
        device_profile="cpu-safe",
        deterministic=True,
    )


def spec(*, tenant_id: str = "tenant-1", job_type: str = "train_dendritic_memory") -> DendriticJobSpecV1:
    return DendriticJobSpecV1(
        tenant_id=tenant_id,
        spec_id="experiment-1",
        job_type=job_type,
        mode="dry_run",
        dataset_manifest_digest="a" * 64,
        base_model_id="mock-local-model",
        base_model_snapshot_digest="b" * 64,
        configuration=config(),
        parent_pack_digests=("c" * 64, "d" * 64) if job_type == "compose_dendritic_memory" else (),
    )


def pack(*, tenant_id: str = "tenant-1", suffix: str = "one", executable: bool = False):
    weights = f"mock-{suffix}".encode()
    manifest = DendriticMemoryPackManifestV1(
        tenant_id=tenant_id,
        pack_id=f"pack-{suffix}",
        base_model_id="mock-local-model",
        base_model_snapshot_digest="b" * 64,
        architecture_version="branch-projection-v1",
        target_layers=(f"model.layers.{0 if suffix == 'one' else 1}",),
        parameter_count=128,
        trainable_parameter_count=128,
        dataset_manifest_digest="a" * 64,
        split_digests={"train": "1" * 64, "validation": "2" * 64, "test": "3" * 64},
        configuration_digest=canonical_digest(config().to_dict()),
        metrics_digest="e" * 64,
        files=(
            {
                "name": "weights.safetensors",
                "sha256": hashlib.sha256(weights).hexdigest(),
                "size_bytes": len(weights),
                "media_type": "application/vnd.safetensors",
            },
        ),
        executable=executable,
    )
    return manifest, {"weights.safetensors": weights}


def evaluation_input(*, accuracy: float, loss: float, pack_digest: str = "f" * 64) -> dict[str, Any]:
    return {
        "dataset_manifest_digest": "a" * 64,
        "test_split_digest": "3" * 64,
        "hardware_digest": "4" * 64,
        "task_family": "structured-planning",
        "trainable_parameter_count": 128,
        "seeds": [1, 2, 3],
        "pack_digest": pack_digest,
        "metrics": {
            "accuracy": accuracy,
            "loss": loss,
            "calibration_error": 0.1,
            "latency_ms": 10,
            "peak_memory_bytes": 1024,
        },
    }


def leakage(*, canary: int = 0) -> dict[str, Any]:
    return {
        "exact_duplicates": 0,
        "normalized_duplicates": 0,
        "canary_secret_reconstructions": canary,
        "untrained_control_reconstructions": 0,
        "paraphrase_overlap_passed": True,
        "ood_passed": True,
    }
