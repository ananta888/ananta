"""NVIDIA runtime execution for the LoRA smoke gate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.lora_training_smoke_files import canonical_sha256, file_sha256
from scripts.lora_training_smoke_release_chain import (
    complete_unsloth_release_chain,
    write_jsonl,
)

_GPU_BACKENDS = frozenset({"peft_trl", "unsloth"})
_MAX_METRIC_DEPTH = 4
_MAX_METRIC_FIELDS = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AdmittedNvidiaDataset:
    dataset_id: str
    dataset_hash: str
    dataset_partition_sha256: str
    recipe_id: str
    source_id: str
    run_id: str
    train_path: Path
    train_sha256: str
    train_rows: int
    validation_path: Path
    validation_sha256: str
    validation_rows: int


def _verified_recipe_path(root: Path, reference: object) -> Path:
    if not isinstance(reference, str) or not reference:
        raise ValueError("nvidia_smoke_dataset_result_invalid")
    unresolved = root / reference
    current = root
    for part in Path(reference).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("nvidia_smoke_dataset_path_invalid")
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("nvidia_smoke_dataset_path_invalid") from exc
    if not resolved.is_file():
        raise ValueError("nvidia_smoke_dataset_path_invalid")
    return resolved


def _jsonl_binding(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    rows = 0
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            if line.strip():
                rows += 1
    return digest.hexdigest(), rows


def load_admitted_nvidia_dataset(result_path: Path) -> AdmittedNvidiaDataset:
    """Load one immutable recipe result without trusting caller-provided paths."""
    try:
        if result_path.is_symlink():
            raise ValueError("nvidia_smoke_dataset_result_invalid")
        resolved_result = result_path.resolve(strict=True)
        value = json.loads(resolved_result.read_text(encoding="utf-8"))
        train_rows = value.get("train_rows") if isinstance(value, Mapping) else None
        validation_rows = value.get("validation_rows") if isinstance(value, Mapping) else None
        if (
            isinstance(train_rows, bool)
            or not isinstance(train_rows, int)
            or isinstance(validation_rows, bool)
            or not isinstance(validation_rows, int)
        ):
            raise ValueError("nvidia_smoke_dataset_binding_invalid")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("nvidia_smoke_dataset_result_invalid") from exc
    if not isinstance(value, Mapping):
        raise ValueError("nvidia_smoke_dataset_result_invalid")
    root = resolved_result.parent.parent.resolve(strict=True)
    recipe_id = str(value.get("recipe_id") or "")
    train = _verified_recipe_path(root, value.get("train_ref"))
    validation = _verified_recipe_path(root, value.get("validation_ref"))
    train_binding = _jsonl_binding(train)
    validation_binding = _jsonl_binding(validation)
    if (
        value.get("schema") != "ananta.unsloth-data-recipe-result.v1"
        or resolved_result.parent.name != recipe_id
        or _SHA256.fullmatch(recipe_id) is None
        or _SHA256.fullmatch(str(value.get("dataset_hash") or "")) is None
        or _SHA256.fullmatch(str(value.get("dataset_partition_sha256") or "")) is None
        or not str(value.get("dataset_id") or "")
        or not str(value.get("source_id") or "").startswith("SRC_")
        or not str(value.get("run_id") or "").startswith("RUN_")
        or train_binding != (str(value.get("train_sha256") or ""), train_rows)
        or validation_binding != (str(value.get("validation_sha256") or ""), validation_rows)
        or train_binding[1] < 1
        or validation_binding[1] < 1
        or train == validation
    ):
        raise ValueError("nvidia_smoke_dataset_binding_invalid")
    return AdmittedNvidiaDataset(
        dataset_id=str(value["dataset_id"]),
        dataset_hash=str(value["dataset_hash"]),
        dataset_partition_sha256=str(value["dataset_partition_sha256"]),
        recipe_id=recipe_id,
        source_id=str(value["source_id"]),
        run_id=str(value["run_id"]),
        train_path=train,
        train_sha256=train_binding[0],
        train_rows=train_binding[1],
        validation_path=validation,
        validation_sha256=validation_binding[0],
        validation_rows=validation_binding[1],
    )


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


def materialize_runtime_gguf(
    *,
    runtime: Any,
    job_id: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    destination: Path,
) -> dict[str, Any]:
    """Atomically copy the single verified GGUF export across the worker boundary."""
    candidates = sorted(
        name for name in artifacts if name.startswith("export-gguf-q4-k-m/") and name.lower().endswith(".gguf")
    )
    if len(candidates) != 1:
        raise ValueError("nvidia_smoke_runtime_gguf_ambiguous")
    if destination.is_symlink() or destination.exists():
        raise ValueError("nvidia_smoke_runtime_export_destination_invalid")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o777)
    if destination.parent.is_symlink():
        raise ValueError("nvidia_smoke_runtime_export_destination_invalid")
    destination.parent.chmod(0o777)
    destination.mkdir(mode=0o777)
    # The export crosses from the unprivileged container UID to the host-side
    # evidence gate.  mkdir applies the worker umask, so make the boundary
    # contract explicit after creation; otherwise the host can validate the
    # GGUF but cannot remove the worker-owned directory deterministically.
    destination.chmod(0o777)
    source, metadata = runtime.artifact(job_id, candidates[0])
    expected_sha256 = str(metadata.get("sha256") or "")
    expected_size = int(metadata.get("size_bytes") or 0)
    target = destination / "model.Q4_K_M.gguf"
    partial = destination / ".model.Q4_K_M.gguf.partial"
    try:
        with source.open("rb") as source_handle, partial.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if partial.stat().st_size != expected_size or file_sha256(partial) != expected_sha256:
            raise ValueError("nvidia_smoke_runtime_gguf_hash_mismatch")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return {
        "format": "gguf",
        "quantization_method": "q4_k_m",
        "filename": target.name,
        "sha256": expected_sha256,
        "size_bytes": expected_size,
    }


def run_nvidia_live_smoke(
    model_path: Path,
    probe: Mapping[str, Any],
    *,
    backend: str = "peft_trl",
    target_modules: Sequence[str],
    timeout_seconds: float,
    runtime_export_dir: Path | None = None,
    dataset_result: Path | None = None,
) -> dict[str, Any]:
    backend = normalize_gpu_backend(backend)
    reset_peak_vram()
    from worker.training.runtime import RuntimeConfiguration, TrainingWorkerRuntime

    try:
        admitted_dataset = load_admitted_nvidia_dataset(dataset_result) if dataset_result is not None else None
    except (OSError, ValueError):
        return {
            "status": "failed",
            "reason_code": "nvidia_smoke_dataset_admission_invalid",
            "retryable": False,
        }
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
        if admitted_dataset is None:
            train_sha, train_count = write_jsonl(dataset_root / "train.jsonl", fixed_train)
            validation_sha, validation_count = write_jsonl(dataset_root / "validation.jsonl", fixed_validation)
        else:
            dataset_root.mkdir()
            shutil.copyfile(admitted_dataset.train_path, dataset_root / "train.jsonl")
            shutil.copyfile(admitted_dataset.validation_path, dataset_root / "validation.jsonl")
            train_sha, train_count = admitted_dataset.train_sha256, admitted_dataset.train_rows
            validation_sha = admitted_dataset.validation_sha256
            validation_count = admitted_dataset.validation_rows
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
                "dataset_id": admitted_dataset.dataset_id if admitted_dataset else "nvidia-smoke-dataset",
                "dataset_version": admitted_dataset.recipe_id if admitted_dataset else "v1",
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
            runtime_export = None
            if backend == "unsloth" and runtime_export_dir is not None:
                runtime_export = materialize_runtime_gguf(
                    runtime=runtime,
                    job_id=str(envelope["job_id"]),
                    artifacts=metadata,
                    destination=runtime_export_dir,
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
                "dataset_provenance": (
                    {
                        "synthetic": False,
                        "dataset_id": admitted_dataset.dataset_id,
                        "dataset_hash": admitted_dataset.dataset_hash,
                        "dataset_partition_sha256": admitted_dataset.dataset_partition_sha256,
                        "recipe_id": admitted_dataset.recipe_id,
                        "source_id": admitted_dataset.source_id,
                        "run_id": admitted_dataset.run_id,
                    }
                    if admitted_dataset
                    else {"synthetic": True}
                ),
                "configuration_sha256": hashlib.sha256(
                    json.dumps(envelope["configuration"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "library_fingerprint_sha256": canonical_sha256(probe["packages"]),
                "gpu_fingerprint_sha256": canonical_sha256(probe["gpu"]),
                "gpu": probe["gpu"],
                "packages": probe["packages"],
                "artifacts": evidence,
                "requested_exports": requested_exports,
                "runtime_export": runtime_export,
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
