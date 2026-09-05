"""NVIDIA runtime execution for the LoRA smoke gate."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.lora_training_smoke_files import canonical_sha256
from scripts.lora_training_smoke_release_chain import (
    complete_unsloth_release_chain,
    write_jsonl,
)

_GPU_BACKENDS = frozenset({"peft_trl", "unsloth"})
_MAX_METRIC_DEPTH = 4
_MAX_METRIC_FIELDS = 64


def normalize_gpu_backend(value: str) -> str:
    backend = str(value or "").strip().lower()
    if backend not in _GPU_BACKENDS:
        raise ValueError(f"unsupported GPU smoke backend: {backend or '<empty>'}")
    return backend


def reset_peak_vram() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except (ImportError, RuntimeError):
        return


def peak_vram() -> dict[str, Any]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"available": False}
        return {
            "available": True,
            "max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "max_memory_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "cuda_runtime": str(getattr(torch.version, "cuda", "") or "") or None,
            "cudnn_version": (
                int(torch.backends.cudnn.version())
                if torch.backends.cudnn.is_available() and torch.backends.cudnn.version() is not None
                else None
            ),
        }
    except (ImportError, RuntimeError):
        return {"available": False}


def bounded_numeric_metrics(value: object, *, depth: int = 0) -> dict[str, Any]:
    """Project controlled numeric metrics without leaking records or unbounded output."""
    if not isinstance(value, Mapping) or depth >= _MAX_METRIC_DEPTH:
        return {}
    projected: dict[str, Any] = {}
    for raw_key in sorted(value, key=str)[:_MAX_METRIC_FIELDS]:
        key = str(raw_key)[:96]
        item = value[raw_key]
        if item is None:
            projected[key] = None
        elif isinstance(item, bool):
            continue
        elif isinstance(item, int):
            projected[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            projected[key] = item
        elif isinstance(item, Mapping):
            nested = bounded_numeric_metrics(item, depth=depth + 1)
            if nested:
                projected[key] = nested
    return projected


def nvidia_runtime_backend(backend: str) -> Any:
    if backend == "unsloth":
        from worker.training.backends.unsloth import UnslothTrainingBackend

        return UnslothTrainingBackend()
    from worker.training.backends.peft_trl import PeftTrlTrainingBackend

    return PeftTrlTrainingBackend()


def run_nvidia_live_smoke(
    model_path: Path,
    probe: Mapping[str, Any],
    *,
    backend: str = "peft_trl",
    target_modules: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    backend = normalize_gpu_backend(backend)
    reset_peak_vram()
    from worker.training.runtime import RuntimeConfiguration, TrainingWorkerRuntime

    fixed_train = (
        {"instruction": "Return the token alpha.", "output": "alpha"},
        {"instruction": "Return the token beta.", "output": "beta"},
        {"instruction": "Return the token gamma.", "output": "gamma"},
        {"instruction": "Return the token delta.", "output": "delta"},
    )
    fixed_validation = (
        {"instruction": "Return the token epsilon.", "output": "epsilon"},
        {"instruction": "Return the token zeta.", "output": "zeta"},
    )
    with tempfile.TemporaryDirectory(prefix="ananta-lora-nvidia-smoke-") as temporary:
        root = Path(temporary)
        dataset_root = root / "datasets"
        workspace_root = root / "workspaces"
        state_root = root / "state"
        (workspace_root / "smoke").mkdir(parents=True)
        state_root.mkdir(parents=True)
        train_sha, train_count = write_jsonl(dataset_root / "train.jsonl", fixed_train)
        validation_sha, validation_count = write_jsonl(dataset_root / "validation.jsonl", fixed_validation)
        envelope = {
            "contract_version": "ananta.lora-training.v1",
            "job_id": "nvidia-live-smoke",
            "attempt_id": "nvidia-live-smoke-attempt-1",
            "fencing_token": 1,
            "correlation_id": "nvidia-live-smoke-correlation",
            "job_type": "train_lora",
            "backend": backend,
            "resource_profile": "nvidia",
            "tenant_scope_digest": hashlib.sha256(b"ananta-nvidia-smoke-tenant-scope-v1").hexdigest(),
            "workspace_ref": "smoke",
            "deadline_epoch_ms": int((time.time() + timeout_seconds) * 1000),
            "base_model": {
                "model_id": "local/nvidia-smoke-model",
                "relative_path": model_path.name,
                "snapshot_hash": probe["model_snapshot_sha256"],
            },
            "dataset": {
                "dataset_id": "nvidia-smoke-dataset",
                "dataset_version": "v1",
                "train": {
                    "relative_path": "train.jsonl",
                    "sha256": train_sha,
                    "record_count": train_count,
                },
                "validation": {
                    "relative_path": "validation.jsonl",
                    "sha256": validation_sha,
                    "record_count": validation_count,
                },
            },
            "configuration": {
                "seed": 1729,
                "max_steps": 1,
                "num_train_epochs": 1.0,
                "learning_rate": 0.0002,
                "train_batch_size": 1,
                "eval_batch_size": 1,
                "gradient_accumulation_steps": 1,
                "eval_steps": 1,
                "save_steps": 1,
                "early_stopping_patience": 0,
                "lora_rank": 4,
                "lora_alpha": 8,
                "lora_dropout": 0.0,
                "max_sequence_length": 128,
                "quantization": "4bit",
                "gradient_checkpointing": False,
                "target_modules": list(target_modules),
            },
        }
        requested_exports: list[dict[str, Any]] = []
        if backend == "unsloth":
            requested_exports = [
                {"format": "adapter"},
                {"format": "merged_16bit"},
                {"format": "gguf", "quantization_method": "q4_k_m"},
            ]
            envelope["exports"] = requested_exports
        runtime = TrainingWorkerRuntime(
            RuntimeConfiguration(
                state_root=state_root,
                workspace_root=workspace_root,
                dataset_root=dataset_root,
                model_root=model_path.parent,
                resource_profile="nvidia",
                max_workers=1,
                max_queue=0,
                max_dataset_bytes=16 * 1024 * 1024,
                max_dataset_records=100,
                isolate_processes=False,
            ),
            {backend: nvidia_runtime_backend(backend)},
        )
        try:
            runtime.submit(envelope)
            deadline = time.monotonic() + timeout_seconds
            status = runtime.status("nvidia-live-smoke")
            while status["status"] not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
                time.sleep(0.25)
                status = runtime.status("nvidia-live-smoke")
            if status["status"] != "succeeded":
                reason = status.get("error") if isinstance(status.get("error"), Mapping) else {}
                return {
                    "status": "failed",
                    "reason_code": str(reason.get("code") or "nvidia_smoke_timeout"),
                    "retryable": bool(reason.get("retryable", False)),
                }
            training_metrics = bounded_numeric_metrics(status.get("metrics"))
            if not training_metrics:
                return {"status": "failed", "reason_code": "nvidia_smoke_training_metrics_missing"}
            expected = {"adapter_model.safetensors", "evaluation.json", "training_manifest.json"}
            export_manifests: dict[str, tuple[str, str | None]] = {}
            if backend == "unsloth":
                export_manifests = {
                    "export-adapter/ananta-export-manifest.json": ("adapter", None),
                    "export-merged-16bit/ananta-export-manifest.json": ("merged_16bit", None),
                    "export-gguf-q4-k-m/ananta-export-manifest.json": ("gguf", "q4_k_m"),
                }
                expected.update(export_manifests)
            metadata = {item["name"]: item for item in status.get("artifacts") or []}
            if not expected.issubset(metadata):
                return {"status": "failed", "reason_code": "nvidia_smoke_artifacts_missing"}
            evidence: dict[str, Any] = {}
            export_evidence: dict[str, Any] = {}
            for name in sorted(expected):
                path, item = runtime.artifact("nvidia-live-smoke", name)
                evidence[name] = {
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                }
                if name == "adapter_model.safetensors":
                    with path.open("rb") as handle:
                        header_size = int.from_bytes(handle.read(8), "little")
                        header = json.loads(handle.read(header_size))
                    if not isinstance(header, dict) or not header:
                        return {"status": "failed", "reason_code": "nvidia_smoke_safetensors_invalid"}
                if name in export_manifests:
                    export_format, quantization_method = export_manifests[name]
                    try:
                        manifest = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        return {"status": "failed", "reason_code": "nvidia_smoke_export_manifest_invalid"}
                    if (
                        not isinstance(manifest, Mapping)
                        or manifest.get("format") != export_format
                        or manifest.get("quantization_method") != quantization_method
                        or not isinstance(manifest.get("artifact_sha256"), str)
                        or re.fullmatch(r"[0-9a-f]{64}", str(manifest["artifact_sha256"])) is None
                        or isinstance(manifest.get("file_count"), bool)
                        or not isinstance(manifest.get("file_count"), int)
                        or int(manifest["file_count"]) < 1
                        or isinstance(manifest.get("total_bytes"), bool)
                        or not isinstance(manifest.get("total_bytes"), int)
                        or int(manifest["total_bytes"]) < 1
                    ):
                        return {"status": "failed", "reason_code": "nvidia_smoke_export_manifest_invalid"}
                    prefix = name.rsplit("/", 1)[0] + "/"
                    exported_items = [
                        artifact
                        for artifact_name, artifact in metadata.items()
                        if artifact_name.startswith(prefix) and artifact_name != name
                    ]
                    if len(exported_items) != int(manifest["file_count"]) or sum(
                        int(artifact["size_bytes"]) for artifact in exported_items
                    ) != int(manifest["total_bytes"]):
                        return {"status": "failed", "reason_code": "nvidia_smoke_export_manifest_mismatch"}
                    export_evidence[export_format] = {
                        "manifest": name,
                        "artifact_sha256": manifest["artifact_sha256"],
                        "file_count": manifest["file_count"],
                        "total_bytes": manifest["total_bytes"],
                        "quantization_method": quantization_method,
                    }
            export_stage = (
                {
                    "status": "passed",
                    "requested_formats": [item["format"] for item in requested_exports],
                    "evidence": export_evidence,
                }
                if backend == "unsloth"
                else {
                    "status": "not_run",
                    "reason_code": "unsloth_export_profile_not_selected",
                }
            )
            return {
                "status": "passed",
                "job_status": status["status"],
                "worker_image_sha256": probe["worker_image"]["sha256"],
                "worker_image_fingerprint_kind": probe["worker_image"]["kind"],
                "image_attestation": dict(probe.get("image_attestation") or {}),
                "versions": dict(probe.get("versions") or {}),
                "peak_vram": peak_vram(),
                "training_metrics": training_metrics,
                "backend": backend,
                "job_identity": {
                    "job_id": envelope["job_id"],
                    "attempt_id": envelope["attempt_id"],
                    "fencing_token": envelope["fencing_token"],
                    "correlation_id": envelope["correlation_id"],
                },
                "model_snapshot_sha256": probe["model_snapshot_sha256"],
                "dataset_sha256": hashlib.sha256(f"{train_sha}:{validation_sha}".encode()).hexdigest(),
                "configuration_sha256": hashlib.sha256(
                    json.dumps(envelope["configuration"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "library_fingerprint_sha256": canonical_sha256(probe["packages"]),
                "gpu_fingerprint_sha256": canonical_sha256(probe["gpu"]),
                "gpu": probe["gpu"],
                "packages": probe["packages"],
                "artifacts": evidence,
                "requested_exports": requested_exports,
                "platform_stage_coverage": complete_unsloth_release_chain(
                    runtime=runtime,
                    training_envelope=envelope,
                    workspace_root=workspace_root,
                    root=root,
                    probe=probe,
                    export_evidence=export_evidence,
                    timeout_seconds=timeout_seconds,
                )
                if backend == "unsloth"
                else {
                    "training": {"status": "passed"},
                    "export": export_stage,
                    "training_evaluation": {
                        "status": "passed",
                        "artifact": "evaluation.json",
                    },
                    "adapter_evaluation": {
                        "status": "not_run",
                        "reason_code": "standalone_adapter_evaluation_not_composed",
                    },
                    "promotion": {
                        "status": "not_run",
                        "reason_code": "hub_promotion_flow_not_composed",
                    },
                    "runtime_load": {
                        "status": "not_run",
                        "reason_code": "provider_runtime_load_not_composed",
                    },
                },
            }
        finally:
            runtime.close()
