#!/usr/bin/env python3
"""Run the reproducible LoRA control-center acceptance gate.

The default gate is network-free and GPU-free.  The NVIDIA live part is only
executed when a local model directory, CUDA device and the complete PEFT/TRL
runtime are present.  Missing hardware is reported as ``not_run`` and is never
represented as a successful live-training proof.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = "artifacts/lora-training-control-center-gate.json"
DEFAULT_COMPATIBILITY_MATRIX = (
    "docs/contracts/unsloth-gpu-compatibility-matrix.v1.json"
)
_GPU_BACKENDS = frozenset({"peft_trl", "unsloth"})
_REQUIRED_UNSLOTH_RUNS = 3
_RUNTIME_IMAGE_DIGEST_PATTERN = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
MOCK_GATE_TESTS = (
    "tests/test_ml_intern_artifact_security_service.py",
    "tests/test_ml_intern_adapter_import_service.py",
    "tests/test_ml_intern_adapter_export_service.py",
    "tests/test_ml_intern_adapter_registry_service.py",
    "tests/test_ml_intern_dataset_catalog_service.py",
    "tests/test_ml_intern_dataset_preview_service.py",
    "tests/test_ml_intern_dataset_split_service.py",
    "tests/test_ml_intern_lora_training_documentation.py",
    "tests/test_ml_intern_training_contract_v2.py",
    "tests/test_ml_intern_training_repository.py",
    "tests/test_ml_intern_training_control_service.py",
    "tests/test_ml_intern_training_reconciliation_service.py",
    "tests/test_ml_intern_training_worker_port.py",
    "tests/test_ml_intern_training_routes_v2.py",
    "tests/test_unsloth_domain_adapters.py",
    "tests/test_unsloth_evaluation_promotion_chain.py",
    "tests/test_unsloth_mcp_adapter.py",
    "tests/test_unsloth_mutation_routes.py",
    "tests/test_unsloth_runtime_handoff_command.py",
    "tests/test_unsloth_runtime_handoff_composition.py",
    "tests/test_unsloth_studio_transport.py",
    "tests/test_unsloth_worker_capability_contract.py",
    "tests/test_ml_intern_unsloth_export_contract.py",
    "tests/worker/test_lora_training_app.py",
    "tests/worker/test_lora_training_backends.py",
    "tests/worker/test_lora_training_contract_schemas.py",
    "tests/worker/test_lora_training_contracts.py",
    "tests/worker/test_lora_training_datasets.py",
    "tests/worker/test_lora_training_deployment.py",
    "tests/worker/test_lora_training_runtime.py",
    "tests/worker/test_lora_training_storage_cleanup.py",
    "tests/worker/test_unsloth_checkpoint_resume.py",
    "tests/worker/test_unsloth_export_composition.py",
    "tests/worker/test_unsloth_exports.py",
    "tests/worker/test_unsloth_platform_backends.py",
    "tests/e2e/test_lora_training_control_plane.py",
)
_MOCK_CAPABILITY_EVIDENCE = {
    "upload": ("tests/test_ml_intern_training_routes_v2.py", "tests/test_ml_intern_dataset_catalog_service.py"),
    "split": ("tests/test_ml_intern_training_routes_v2.py", "tests/test_ml_intern_dataset_split_service.py"),
    "validation": ("tests/test_ml_intern_training_routes_v2.py", "tests/test_ml_intern_dataset_catalog_service.py"),
    "async_delegation": (
        "tests/test_ml_intern_training_control_service.py",
        "tests/e2e/test_lora_training_control_plane.py",
    ),
    "events": ("tests/test_ml_intern_training_routes_v2.py", "tests/test_ml_intern_training_repository.py"),
    "cancel": ("tests/test_ml_intern_training_control_service.py", "tests/worker/test_lora_training_runtime.py"),
    "evaluation": ("tests/e2e/test_lora_training_control_plane.py", "tests/test_ml_intern_training_routes_v2.py"),
    "registry": ("tests/test_ml_intern_adapter_registry_service.py", "tests/test_ml_intern_training_routes_v2.py"),
    "export": ("tests/test_ml_intern_adapter_export_service.py", "tests/test_ml_intern_training_routes_v2.py"),
    "runtime_handoff": (
        "tests/test_unsloth_runtime_handoff_command.py",
        "tests/test_unsloth_runtime_handoff_composition.py",
    ),
    "studio_contract": ("tests/test_unsloth_studio_transport.py",),
    "mcp_contract": ("tests/test_unsloth_mcp_adapter.py",),
    "modalities": (
        "tests/test_unsloth_worker_capability_contract.py",
        "tests/worker/test_unsloth_platform_backends.py",
    ),
}
_PACKAGE_NAMES = ("torch", "transformers", "datasets", "peft", "trl", "safetensors", "bitsandbytes")
_SAFE_MODEL_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _python_executable() -> str:
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    safe_env = dict(os.environ)
    safe_env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
    )
    return subprocess.run(
        list(command),
        cwd=str(ROOT),
        env=safe_env,
        check=False,
        capture_output=True,
        text=True,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("tree hash does not admit symbolic links")
    if path.is_file():
        return _file_sha256(path)
    if not path.is_dir():
        raise ValueError("tree hash requires a regular file or directory")
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise ValueError("tree hash does not admit symbolic links")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise ValueError("tree hash does not admit special filesystem entries")
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise ValueError("tree hash requires a non-empty file tree")
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _suite_sha256(paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(path).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _docker_copy_tree_paths(relative_root: str) -> tuple[str, ...]:
    """List the regular source files copied from a Docker build-context tree."""

    root = ROOT / relative_root
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Docker build-input tree is unavailable: {relative_root}")
    paths: list[str] = []
    for child in sorted(root.rglob("*")):
        relative_child = child.relative_to(root)
        if "__pycache__" in relative_child.parts or child.suffix in {".pyc", ".pyo"}:
            continue
        if child.is_symlink():
            raise ValueError(f"Docker build-input tree contains a symbolic link: {relative_root}")
        if child.is_file():
            paths.append(child.relative_to(ROOT).as_posix())
        elif not child.is_dir():
            raise ValueError(f"Docker build-input tree contains a special entry: {relative_root}")
    if not paths:
        raise ValueError(f"Docker build-input tree is empty: {relative_root}")
    return tuple(paths)


def _worker_image_build_input_paths() -> tuple[str, ...]:
    """Return every source input used by the LoRA worker Dockerfile."""

    fixed_inputs = (
        ".dockerignore",
        "docker/compose-next/Dockerfile.lora-training-worker",
        "docker/compose-next/requirements.runtime-http.txt",
        "docker/compose-next/requirements.lora-training-cpu.txt",
        "docker/compose-next/requirements.lora-training-nvidia.txt",
        "worker/__init__.py",
        "worker/runtime/__init__.py",
        "worker/runtime/lora_training_app.py",
    )
    return (
        *fixed_inputs,
        *_docker_copy_tree_paths("ananta_contracts"),
        *_docker_copy_tree_paths("worker/training"),
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _worker_image_fingerprint() -> dict[str, str]:
    """Return an honest image digest or its reproducible build-input fallback."""

    declared = str(os.environ.get("ANANTA_LORA_WORKER_IMAGE_SHA256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", declared):
        return {"sha256": declared, "kind": "declared_image_digest"}
    return {
        "sha256": _suite_sha256(_worker_image_build_input_paths()),
        "kind": "reproducible_build_input_digest",
    }


def _mock_gate(runner: CommandRunner) -> dict[str, Any]:
    command = [_python_executable(), "-m", "pytest", "-q", *MOCK_GATE_TESTS]
    result = runner(command)
    combined = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"(?P<count>\d+) passed", combined)
    status = "passed" if result.returncode == 0 else "failed"
    return {
        "status": status,
        "returncode": int(result.returncode),
        "tests_passed": int(match.group("count")) if match else None,
        "suite_sha256": _suite_sha256(MOCK_GATE_TESTS),
        "worker_image": _worker_image_fingerprint(),
        "capabilities_proven": sorted(_MOCK_CAPABILITY_EVIDENCE) if status == "passed" else [],
        "capability_evidence": {
            capability: list(paths) for capability, paths in sorted(_MOCK_CAPABILITY_EVIDENCE.items())
        },
        "reproduce": list(command),
    }


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _normalize_gpu_backend(value: str) -> str:
    backend = str(value or "").strip().lower()
    if backend not in _GPU_BACKENDS:
        raise ValueError(f"unsupported GPU smoke backend: {backend or '<empty>'}")
    return backend


def _normalize_evidence_ids(
    *,
    src_ids: Sequence[str] = (),
    run_ids: Sequence[str] = (),
) -> dict[str, Any]:
    def expand(values: Sequence[str]) -> list[str]:
        return [
            candidate.strip()
            for raw in values
            for candidate in raw.split(",")
        ]

    try:
        sources = list(normalize_source_ids(expand(src_ids)))
        runs = list(normalize_run_ids(expand(run_ids)))
    except MlInternTrainingContractError as exc:
        raise MlInternTrainingContractError(
            exc.reason_code,
            f"invalid evidence identifier: {exc}",
            status_code=exc.status_code,
        ) from exc
    missing = [
        name
        for name, values in (("src_ids", sources), ("run_ids", runs))
        if not values
    ]
    return {
        "src_ids": sources,
        "run_ids": runs,
        "complete": not missing,
        "missing": missing,
    }


def _runtime_image_digest(value: str | None) -> str | None:
    supplied = str(value or "").strip().lower()
    if not supplied:
        return None
    match = _RUNTIME_IMAGE_DIGEST_PATTERN.fullmatch(supplied)
    if match is None:
        raise ValueError("runtime image digest must be sha256:<64 lowercase hex>")
    return f"sha256:{match.group(1)}"


def _worker_image_attestation(runtime_image_digest: str | None = None) -> dict[str, Any]:
    runtime_digest = _runtime_image_digest(
        runtime_image_digest or os.environ.get("ANANTA_LORA_WORKER_IMAGE_SHA256")
    )
    return {
        "build_input_sha256": _suite_sha256(_worker_image_build_input_paths()),
        "runtime_image_digest": runtime_digest,
        "runtime_image_digest_supplied": runtime_digest is not None,
    }


def _installed_runtime_versions() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for package in (
        "unsloth",
        "unsloth-zoo",
        "torch",
        "transformers",
        "trl",
        "peft",
        "bitsandbytes",
        "safetensors",
    ):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "python": platform.python_version(),
        "packages": packages,
    }


def _compatibility_matrix_entry(
    matrix_path: Path | None,
    entry_id: str | None,
) -> dict[str, Any]:
    if matrix_path is None or not str(entry_id or "").strip():
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_entry_not_configured",
        }
    try:
        raw = matrix_path.read_bytes()
        matrix = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_unavailable",
        }
    entries = matrix.get("entries") if isinstance(matrix, Mapping) else None
    if (
        not isinstance(entries, list)
        or matrix.get("schema")
        != "ananta.unsloth-gpu-compatibility-matrix.v1"
    ):
        return {
            "status": "failed",
            "reason_code": "compatibility_matrix_invalid",
        }
    selected = next(
        (
            dict(candidate)
            for candidate in entries
            if isinstance(candidate, Mapping)
            and candidate.get("id") == entry_id
        ),
        None,
    )
    if selected is None:
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_entry_unknown",
        }
    return {
        "status": "selected",
        "entry": selected,
        "entry_id": entry_id,
        "matrix_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _compatibility_attestation(
    selection: Mapping[str, Any],
    *,
    probe: Mapping[str, Any],
    versions: Mapping[str, Any],
    image_attestation: Mapping[str, Any],
    required_runs: int,
    completed_runs: int | None = None,
) -> dict[str, Any]:
    if selection.get("status") != "selected":
        return dict(selection)
    entry = dict(selection.get("entry") or {})
    reasons: list[str] = []
    expected_python = str(entry.get("python") or "")
    if expected_python and str(versions.get("python") or "") != expected_python:
        reasons.append("compatibility_python_mismatch")
    approved_model_basenames = tuple(
        str(value)
        for value in entry.get("approved_model_basenames") or ()
        if str(value)
    )
    if (
        approved_model_basenames
        and str(probe.get("model_basename") or "") not in approved_model_basenames
    ):
        reasons.append("compatibility_model_not_approved")
    packages = dict(versions.get("packages") or {})
    expected_packages = dict(entry.get("packages") or {})
    for package, expected in sorted(expected_packages.items()):
        observed = packages.get(package)
        if observed != expected:
            reasons.append(f"compatibility_{package.replace('-', '_')}_mismatch")
    expected_cuda = str(entry.get("cuda_runtime") or "")
    if expected_cuda and str(probe.get("cuda_runtime") or "") != expected_cuda:
        reasons.append("compatibility_cuda_runtime_mismatch")
    minimum_driver = str(entry.get("minimum_nvidia_driver") or "")
    gpu_rows = list(probe.get("gpu") or [])
    observed_driver = (
        str(gpu_rows[0].get("driver") or "")
        if gpu_rows and isinstance(gpu_rows[0], Mapping)
        else ""
    )
    if minimum_driver and (
        not observed_driver
        or _version_tuple(observed_driver) < _version_tuple(minimum_driver)
    ):
        reasons.append("compatibility_nvidia_driver_mismatch")
    if image_attestation.get("runtime_image_digest_supplied") is not True:
        reasons.append("runtime_image_digest_missing")
    if required_runs != int(entry.get("required_deterministic_runs") or 0):
        reasons.append("compatibility_required_run_count_mismatch")
    if (
        completed_runs is not None
        and completed_runs != required_runs
    ):
        reasons.append("deterministic_run_count_incomplete")
    status = "passed" if not reasons else (
        "not_run"
        if reasons == ["runtime_image_digest_missing"]
        else "failed"
    )
    return {
        "schema": "ananta.unsloth-profile-attestation.v1",
        "status": status,
        "reason_codes": reasons,
        "profile_id": selection.get("entry_id"),
        "matrix_sha256": selection.get("matrix_sha256"),
        "runtime_image_digest": image_attestation.get(
            "runtime_image_digest"
        ),
        "build_input_sha256": image_attestation.get("build_input_sha256"),
        "required_runs": required_runs,
        "completed_runs": completed_runs,
        "observed": {
            "packages": packages,
            "cuda_runtime": probe.get("cuda_runtime"),
            "nvidia_driver": observed_driver or None,
        },
        "formats": list(entry.get("formats") or []),
    }


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(
        int(candidate)
        for candidate in re.findall(r"\d+", str(value or ""))[:4]
    )


def _reset_peak_vram() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except (ImportError, RuntimeError):
        return


def _peak_vram() -> dict[str, Any]:
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


def _support_claim(
    *,
    backend: str,
    nvidia_result: Mapping[str, Any],
    evidence_ids: Mapping[str, Any],
    image_attestation: Mapping[str, Any],
    versions: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if backend != "unsloth":
        reasons.append("unsloth_profile_not_selected")
    if nvidia_result.get("status") != "passed":
        reasons.append("unsloth_gpu_smoke_not_passed")
    if not evidence_ids.get("complete"):
        reasons.append("source_or_run_ids_missing")
    if not image_attestation.get("runtime_image_digest_supplied"):
        reasons.append("runtime_image_digest_missing")
    packages = versions.get("packages")
    if not isinstance(packages, Mapping) or not packages.get("unsloth"):
        reasons.append("unsloth_version_unverified")
    if backend == "unsloth":
        stage_coverage = nvidia_result.get("platform_stage_coverage")
        required_stages = {
            "training": "unsloth_training_not_verified",
            "export": "unsloth_exports_not_verified",
            "training_evaluation": "unsloth_training_evaluation_not_verified",
            "adapter_evaluation": "unsloth_adapter_evaluation_not_verified",
            "promotion": "unsloth_promotion_not_verified",
            "runtime_load": "unsloth_runtime_load_not_verified",
            "rollback": "unsloth_runtime_rollback_not_verified",
            "tamper_negative_paths": "unsloth_tamper_negative_paths_not_verified",
        }
        for stage, reason_code in required_stages.items():
            stage_result = stage_coverage.get(stage) if isinstance(stage_coverage, Mapping) else None
            if not isinstance(stage_result, Mapping) or stage_result.get("status") != "passed":
                reasons.append(reason_code)
        attestation = nvidia_result.get("compatibility_attestation")
        if (
            not isinstance(attestation, Mapping)
            or attestation.get("status") != "passed"
        ):
            reasons.append("unsloth_compatibility_profile_not_attested")
        if int(nvidia_result.get("deterministic_run_count") or 0) != (
            _REQUIRED_UNSLOTH_RUNS
        ):
            reasons.append("unsloth_deterministic_runs_incomplete")
    return {
        "schema": "ananta.unsloth-support-claim.v1",
        "verified": not reasons,
        "reason_codes": reasons,
        "src_ids": list(evidence_ids.get("src_ids") or ()),
        "run_ids": list(evidence_ids.get("run_ids") or ()),
    }


def _nvidia_runtime_backend(backend: str) -> Any:
    if backend == "unsloth":
        from worker.training.backends.unsloth import UnslothTrainingBackend

        return UnslothTrainingBackend()
    from worker.training.backends.peft_trl import PeftTrlTrainingBackend

    return PeftTrlTrainingBackend()


def _nvidia_probe(
    model_path: Path | None,
    runner: CommandRunner,
    *,
    backend: str = "peft_trl",
) -> tuple[dict[str, Any], Path | None]:
    backend = _normalize_gpu_backend(backend)
    if backend == "unsloth":
        try:
            importlib.metadata.version("unsloth")
        except importlib.metadata.PackageNotFoundError:
            return {"status": "not_run", "reason_code": "unsloth_dependency_unavailable"}, None
    if model_path is None:
        return {"status": "not_run", "reason_code": "local_model_not_configured"}, None
    expanded = model_path.expanduser()
    if expanded.is_symlink():
        return {"status": "not_run", "reason_code": "local_model_path_not_admitted"}, None
    try:
        resolved = expanded.resolve(strict=True)
    except OSError:
        return {"status": "not_run", "reason_code": "local_model_missing"}, None
    if not resolved.is_dir() or resolved.is_symlink() or not _SAFE_MODEL_BASENAME.fullmatch(resolved.name):
        return {"status": "not_run", "reason_code": "local_model_path_not_admitted"}, None

    try:
        gpu = runner(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
    except OSError:
        return {"status": "not_run", "reason_code": "nvidia_device_unavailable"}, None
    if gpu.returncode != 0 or not gpu.stdout.strip():
        return {"status": "not_run", "reason_code": "nvidia_device_unavailable"}, None
    missing = [name for name in _PACKAGE_NAMES if importlib.util.find_spec(name) is None]
    if missing:
        return {
            "status": "not_run",
            "reason_code": "nvidia_training_dependencies_unavailable",
            "missing_packages": missing,
        }, None
    try:
        import torch
    except ImportError:
        return {"status": "not_run", "reason_code": "torch_unavailable"}, None
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        return {"status": "not_run", "reason_code": "cuda_runtime_unavailable"}, None
    gpu_rows = []
    for line in gpu.stdout.splitlines()[:8]:
        name, driver, memory = [part.strip() for part in line.split(",", maxsplit=2)]
        gpu_rows.append({"name": name[:160], "driver": driver[:64], "memory_mib": int(memory)})
    try:
        snapshot_hash = _tree_sha256(resolved)
    except (OSError, ValueError):
        return {"status": "not_run", "reason_code": "local_model_tree_not_admitted"}, None
    return {
        "status": "ready",
        "gpu": gpu_rows,
        "packages": _package_versions(),
        "model_snapshot_sha256": snapshot_hash,
        "worker_image": _worker_image_fingerprint(),
        "cuda_runtime": str(getattr(torch.version, "cuda", "") or "") or None,
    }, resolved


def _terminal_runtime_status(
    runtime: Any,
    job_id: str,
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    status = runtime.status(job_id)
    while (
        status["status"] not in {"succeeded", "failed", "cancelled"}
        and time.monotonic() < deadline
    ):
        time.sleep(0.25)
        status = runtime.status(job_id)
    return status


def _observe_rejection(call) -> str:
    try:
        call()
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None)
        if isinstance(reason_code, str) and reason_code:
            return reason_code
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code
        return "tamper_rejected_without_stable_code"
    return "tamper_unexpectedly_accepted"


def _transition_tamper_checks(
    chain: Mapping[str, str],
    *,
    observed_rejections: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    reason_codes = {
        "dataset_to_training": "dataset_hash_mismatch",
        "model_to_training": "base_model_hash_mismatch",
        "adapter_to_export": "adapter_hash_mismatch",
        "export_to_evaluation": "promotion_execution_hash_mismatch",
        "evaluation_to_promotion": "promotion_provenance_mismatch",
        "promotion_to_runtime": "runtime_handoff_promotion_binding_mismatch",
        "runtime_to_rollback": "runtime_endpoint_revision_conflict",
    }
    observations = dict(observed_rejections or {})
    results: dict[str, Any] = {}
    for transition, reason_code in reason_codes.items():
        expected = str(chain.get(transition) or "")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            results[transition] = {
                "status": "not_run",
                "reason_code": "transition_evidence_missing",
            }
            continue
        observed = str(observations.get(transition) or "")
        if not observed:
            results[transition] = {
                "status": "not_run",
                "reason_code": "transition_rejection_not_observed",
                "expected_reason_code": reason_code,
            }
            continue
        results[transition] = {
            "status": "passed"
            if hmac.compare_digest(observed, reason_code)
            else "failed",
            "reason_code": observed,
            "expected_reason_code": reason_code,
        }
    return results


def _tampered_sha256(value: object) -> str:
    digest = str(value or "")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("tamper source digest is invalid")
    return ("0" if digest[0] != "0" else "1") + digest[1:]


def _runtime_rejection_code(
    runtime: Any,
    envelope: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> str | None:
    try:
        runtime.submit(envelope)
        status = _terminal_runtime_status(
            runtime,
            str(envelope["job_id"]),
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - acceptance observation
        value = str(
            getattr(exc, "reason_code", "")
            or getattr(exc, "code", "")
            or ""
        ).strip()
        return value or None
    error = (
        status.get("error")
        if isinstance(status.get("error"), Mapping)
        else {}
    )
    return str(error.get("code") or "").strip() or None


class _SmokeAudit:
    def record(self, **_values: Any) -> None:
        return None


class _SmokePromotionPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def promote(self, **values: Any) -> int:
        self.calls.append(dict(values))
        return int(values["expected_revision"]) + 1


class _SmokeEvaluationPort:
    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot

    def get(self, *, tenant_id: str, evaluation_id: str):
        if (
            tenant_id != self._snapshot.tenant_id
            or evaluation_id != self._snapshot.evaluation_id
        ):
            return None
        return self._snapshot


class _SmokeRuntimeTasks:
    def __init__(self, endpoints: Any) -> None:
        self._endpoints = endpoints

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        if task_type != "ml.runtime.artifact_handoff":
            raise ValueError("unexpected runtime task type")
        task_id = f"smoke-handoff-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}"
        self._endpoints.apply_handoff(
            tenant_id=tenant_id,
            endpoint_id=str(payload["endpoint_id"]),
            expected_revision=int(payload["expected_endpoint_revision"]),
            task_id=task_id,
            idempotency_key=idempotency_key,
            manifest=payload,
        )
        return task_id


def _complete_unsloth_release_chain(
    *,
    runtime: Any,
    training_envelope: Mapping[str, Any],
    workspace_root: Path,
    root: Path,
    probe: Mapping[str, Any],
    export_evidence: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    evidence_ids = dict(probe.get("evidence_ids") or {})
    source_ids = tuple(evidence_ids.get("src_ids") or ())
    run_ids = tuple(evidence_ids.get("run_ids") or ())
    base = {
        "training": {"status": "passed"},
        "export": {
            "status": "passed",
            "requested_formats": ["adapter", "merged_16bit", "gguf"],
            "evidence": dict(export_evidence),
        },
        "training_evaluation": {
            "status": "passed",
            "artifact": "evaluation.json",
        },
    }
    observed_rejections: dict[str, str] = {}
    dataset_tamper = json.loads(json.dumps(training_envelope))
    dataset_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-dataset",
            "attempt_id": "tamper-dataset-attempt-1",
            "correlation_id": "tamper-dataset-correlation",
            "fencing_token": 101,
        }
    )
    dataset_tamper["dataset"]["train"]["sha256"] = (
        _tampered_sha256(
            dataset_tamper["dataset"]["train"]["sha256"]
        )
    )
    observed = _runtime_rejection_code(
        runtime,
        dataset_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["dataset_to_training"] = observed
    model_tamper = json.loads(json.dumps(training_envelope))
    model_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-model",
            "attempt_id": "tamper-model-attempt-1",
            "correlation_id": "tamper-model-correlation",
            "fencing_token": 102,
        }
    )
    model_tamper["base_model"]["snapshot_hash"] = (
        _tampered_sha256(
            model_tamper["base_model"]["snapshot_hash"]
        )
    )
    observed = _runtime_rejection_code(
        runtime,
        model_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["model_to_training"] = observed
    if not source_ids or not run_ids:
        missing = {
            "status": "not_run",
            "reason_code": "source_or_run_ids_missing",
        }
        return {
            **base,
            "adapter_evaluation": missing,
            "promotion": missing,
            "runtime_load": missing,
            "rollback": missing,
            "tamper_negative_paths": {
                "status": "not_run",
                "reason_code": "release_chain_incomplete",
            },
        }
    try:
        adapter_weights, _ = runtime.artifact(
            str(training_envelope["job_id"]),
            "adapter_model.safetensors",
        )
        adapter_config, _ = runtime.artifact(
            str(training_envelope["job_id"]),
            "adapter_config.json",
        )
        evaluation_adapter = workspace_root / "smoke" / "release-evaluation-adapter"
        evaluation_adapter.mkdir(parents=True, exist_ok=False)
        shutil.copy2(adapter_weights, evaluation_adapter / adapter_weights.name)
        shutil.copy2(adapter_config, evaluation_adapter / adapter_config.name)
        adapter_sha256 = _tree_sha256(evaluation_adapter)
    except (OSError, KeyError, ValueError):
        failed = {
            "status": "failed",
            "reason_code": "release_chain_adapter_materialization_failed",
        }
        return {
            **base,
            "adapter_evaluation": failed,
            "promotion": {"status": "not_run", "reason_code": "adapter_evaluation_failed"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    evaluation_id = "nvidia-live-smoke-evaluation"
    evaluation_envelope = {
        "contract_version": training_envelope["contract_version"],
        "job_id": evaluation_id,
        "attempt_id": "nvidia-live-smoke-evaluation-attempt-1",
        "fencing_token": 2,
        "correlation_id": "nvidia-live-smoke-evaluation-correlation",
        "job_type": "evaluate_existing_adapter",
        "backend": "unsloth",
        "resource_profile": "nvidia",
        "tenant_scope_digest": training_envelope["tenant_scope_digest"],
        "workspace_ref": "smoke",
        "deadline_epoch_ms": int((time.time() + timeout_seconds) * 1000),
        "base_model": training_envelope["base_model"],
        "adapter": {
            "adapter_id": "nvidia-live-smoke-adapter",
            "relative_path": "release-evaluation-adapter",
            "sha256": adapter_sha256,
        },
        "validation_dataset": {
            "dataset_id": training_envelope["dataset"]["dataset_id"],
            "dataset_version": training_envelope["dataset"]["dataset_version"],
            "validation": training_envelope["dataset"]["validation"],
        },
        "configuration": {
            "seed": 1729,
            "batch_size": 1,
            "max_sequence_length": 128,
            "max_samples": 2,
            "quantization": "4bit",
            "scorer_name": "generic",
        },
    }
    adapter_tamper = json.loads(json.dumps(evaluation_envelope))
    adapter_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-adapter",
            "attempt_id": "tamper-adapter-attempt-1",
            "correlation_id": "tamper-adapter-correlation",
            "fencing_token": 103,
        }
    )
    adapter_tamper["adapter"]["sha256"] = _tampered_sha256(
        adapter_tamper["adapter"]["sha256"]
    )
    observed = _runtime_rejection_code(
        runtime,
        adapter_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["adapter_to_export"] = observed
    runtime.submit(evaluation_envelope)
    evaluation_status = _terminal_runtime_status(
        runtime,
        evaluation_id,
        timeout_seconds=timeout_seconds,
    )
    if evaluation_status.get("status") != "succeeded":
        failed = {
            "status": "failed",
            "reason_code": str(
                dict(evaluation_status.get("error") or {}).get("code")
                or "standalone_adapter_evaluation_failed"
            ),
        }
        return {
            **base,
            "adapter_evaluation": failed,
            "promotion": {"status": "not_run", "reason_code": "adapter_evaluation_failed"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    evaluation_manifest_path, evaluation_manifest_metadata = runtime.artifact(
        evaluation_id,
        "evaluation_manifest.json",
    )
    evaluation_manifest_sha256 = str(
        evaluation_manifest_metadata["sha256"]
    )
    dataset_sha256 = hashlib.sha256(
        (
            f"{training_envelope['dataset']['train']['sha256']}:"
            f"{training_envelope['dataset']['validation']['sha256']}"
        ).encode()
    ).hexdigest()
    export_sha256 = str(
        dict(export_evidence.get("adapter") or {}).get("artifact_sha256") or ""
    )
    if re.fullmatch(r"[0-9a-f]{64}", export_sha256) is None:
        return {
            **base,
            "adapter_evaluation": {
                "status": "passed",
                "manifest_sha256": evaluation_manifest_sha256,
            },
            "promotion": {"status": "failed", "reason_code": "export_hash_missing"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    from agent.services.integration_registry_service import IntegrationRegistryService
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.unsloth_evaluation_promotion_service import (
        EvaluationSnapshot,
        PromotionRequest,
        UnslothEvaluationPromotionService,
    )
    from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
    from agent.services.unsloth_runtime_endpoint_registry_service import (
        SqliteRuntimeEndpointRegistry,
    )
    from agent.services.unsloth_runtime_handoff_service import (
        RuntimeArtifact,
        RuntimeHandoffRequest,
        UnslothRuntimeHandoffService,
    )

    promotion_port = _SmokePromotionPort()
    snapshot = EvaluationSnapshot(
        evaluation_id=evaluation_id,
        tenant_id="nvidia-smoke-tenant",
        artifact_id="nvidia-live-smoke-adapter",
        artifact_sha256=adapter_sha256,
        dataset_hash=dataset_sha256,
        state="passed",
        metrics={"quality_gate": 1.0},
        source_ids=source_ids,
        run_ids=run_ids,
        job_id=evaluation_id,
        attempt_id=str(evaluation_envelope["attempt_id"]),
        fencing_token_digest=hashlib.sha256(b"2").hexdigest(),
        base_model_id=str(training_envelope["base_model"]["model_id"]),
        base_model_sha256=str(training_envelope["base_model"]["snapshot_hash"]),
        adapter_id="nvidia-live-smoke-adapter",
        adapter_sha256=adapter_sha256,
        export_sha256=export_sha256,
    )
    evidence_registry = ProvidedEvidenceRegistry(
        source_ids=source_ids,
        run_ids=run_ids,
    )
    promotion_service = UnslothEvaluationPromotionService(
        evaluations=_SmokeEvaluationPort(snapshot),
        promotions=promotion_port,
        evidence=evidence_registry,
        audit=_SmokeAudit(),
    )
    promotion_request_values = {
        "tenant_id": snapshot.tenant_id,
        "artifact_id": snapshot.artifact_id,
        "artifact_sha256": snapshot.artifact_sha256,
        "dataset_hash": snapshot.dataset_hash,
        "evaluation_id": snapshot.evaluation_id,
        "minimum_metrics": {"quality_gate": 1.0},
        "expected_registry_revision": 1,
        "job_id": snapshot.job_id,
        "attempt_id": snapshot.attempt_id,
        "fencing_token_digest": snapshot.fencing_token_digest,
        "base_model_id": snapshot.base_model_id,
        "base_model_sha256": snapshot.base_model_sha256,
        "adapter_id": snapshot.adapter_id,
        "adapter_sha256": snapshot.adapter_sha256,
        "export_sha256": snapshot.export_sha256,
    }

    def _promotion_request(**overrides):
        values = dict(promotion_request_values)
        values.update(overrides)
        return PromotionRequest(**values)

    observed_rejections["export_to_evaluation"] = _observe_rejection(
        lambda: promotion_service.plan(
            _promotion_request(
                export_sha256=_tampered_sha256(snapshot.export_sha256)
            )
        )
    )
    observed_rejections["evaluation_to_promotion"] = _observe_rejection(
        lambda: promotion_service.plan(
            _promotion_request(
                artifact_sha256=_tampered_sha256(snapshot.artifact_sha256)
            )
        )
    )
    promotion_plan = promotion_service.plan(_promotion_request())
    promoted_revision = promotion_service.promote(
        promotion_plan,
        confirmation_digest=promotion_plan.confirmation_digest,
    )
    descriptors = IntegrationRegistryService().normalize_runtime_endpoint_descriptor(
        provider_descriptor={
            "provider_id": "nvidia-smoke-provider",
            "provider_type": "local-openai-compatible",
            "model_id": "local/nvidia-smoke-model",
            "provider_revision": "attested-profile",
            "capabilities": {
                "openai_chat": True,
                "openai_responses": False,
                "anthropic_messages": False,
                "streaming": False,
                "tools": False,
                "structured_output": False,
            },
            "limits": {
                "timeout_seconds": 120,
                "context_tokens": 128,
                "max_output_tokens": 32,
                "stream_idle_timeout_seconds": 30,
            },
        },
        endpoint_descriptor={
            "endpoint_id": "nvidia-smoke-endpoint",
            "display_name": "Attested NVIDIA smoke endpoint",
            "routing_key": "nvidia-smoke",
        },
    )
    endpoints = SqliteRuntimeEndpointRegistry(root / "runtime-endpoints.sqlite3")
    baseline_manifest = {
        "schema_version": 2,
        "tenant_id": snapshot.tenant_id,
        "endpoint_id": "nvidia-smoke-endpoint",
        "provider": "nvidia-smoke-provider",
        "artifact": {
            "artifact_id": "baseline-model",
            "artifact_sha256": snapshot.base_model_sha256,
            "format": "adapter",
        },
        "provider_descriptor": descriptors["provider"],
        "endpoint_descriptor": descriptors["endpoint"],
        "api_capabilities": descriptors["api_capabilities"],
        "limits": descriptors["limits"],
        "source_ids": list(source_ids),
        "run_ids": list(run_ids),
        "job_id": evaluation_id,
        "attempt_id": snapshot.attempt_id,
        "fencing_token_digest": snapshot.fencing_token_digest,
        "reason_sha256": hashlib.sha256(b"baseline endpoint").hexdigest(),
        "expected_endpoint_revision": 0,
        "fallback": None,
    }
    endpoints.apply_handoff(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        expected_revision=0,
        task_id="nvidia-smoke-baseline-task",
        idempotency_key="nvidia-smoke-baseline",
        manifest=baseline_manifest,
    )
    handoff_service = UnslothRuntimeHandoffService(
        tasks=_SmokeRuntimeTasks(endpoints),
        audit=_SmokeAudit(),
        evidence=evidence_registry,
    )
    from agent.services.ml_intern_training_repository_port import (
        MlInternTrainingPrincipal,
    )
    from agent.services.unsloth_runtime_handoff_composition import (
        UnslothRuntimeHandoffMutationExecutor,
    )

    promoted_export_sha256 = snapshot.export_sha256
    resolved_export_sha256 = _tampered_sha256(promoted_export_sha256)

    class _TamperedPromotionAdapterRegistry:
        def get(self, resource_id, *, tenant_id, owner_subject):
            return type(
                "_TamperedPromotionRecord",
                (),
                {
                    "status": "approved",
                    "provenance_verified": True,
                    "promotion_history": [
                        {
                            "schema": "ananta.adapter-promotion-history.v1",
                            "promotion_id": "promotion-tamper-probe",
                            "artifact_sha256": snapshot.adapter_sha256,
                            "evidence": {
                                "adapter_id": snapshot.adapter_id,
                                "adapter_sha256": snapshot.adapter_sha256,
                                "base_model_id": snapshot.base_model_id,
                                "export_sha256": promoted_export_sha256,
                                "source_ids": list(snapshot.source_ids),
                                "run_ids": list(snapshot.run_ids),
                            },
                        }
                    ],
                    "source_ids": list(snapshot.source_ids),
                    "run_ids": list(snapshot.run_ids),
                    "artifact_sha256": snapshot.adapter_sha256,
                    "adapter_id": snapshot.adapter_id,
                    "base_model": snapshot.base_model_id,
                },
            )()

    class _TamperedPromotionExportService:
        def resolve_export(self, artifact_id, *, tenant_id, owner_subject):
            return Path("/unused"), resolved_export_sha256

    tampered_handoff_executor = UnslothRuntimeHandoffMutationExecutor(
        handoff=handoff_service,
        endpoints=endpoints,
        export_service=_TamperedPromotionExportService(),
        adapter_registry=_TamperedPromotionAdapterRegistry(),
        integrations=object(),
    )
    observed_rejections["promotion_to_runtime"] = _observe_rejection(
        lambda: tampered_handoff_executor.preview_operation(
            principal=MlInternTrainingPrincipal(
                tenant_id=snapshot.tenant_id,
                subject="lora-smoke-tamper-probe",
            ),
            resource_id=snapshot.adapter_id,
            reason="Acceptance tamper probe for promotion binding",
            operation_payload={
                "promoted_artifact_id": "export-tamper-probe",
                "promoted_artifact_sha256": resolved_export_sha256,
                "source_ids": list(snapshot.source_ids),
                "run_ids": list(snapshot.run_ids),
                "provider_descriptor": {},
                "endpoint_descriptor": {},
                "expected_endpoint_revision": 0,
            },
        )
    )
    handoff_plan = handoff_service.plan(
        RuntimeHandoffRequest(
            tenant_id=snapshot.tenant_id,
            endpoint_id="nvidia-smoke-endpoint",
            provider="nvidia-smoke-provider",
            artifact=RuntimeArtifact(
                artifact_id="nvidia-smoke-export",
                tenant_id=snapshot.tenant_id,
                artifact_sha256=export_sha256,
                registry_state="promoted",
                verification_state="verified",
                format="adapter",
            ),
            source_ids=source_ids,
            run_ids=run_ids,
            expected_endpoint_revision=1,
            provider_descriptor=descriptors["provider"],
            endpoint_descriptor=descriptors["endpoint"],
            api_capabilities=descriptors["api_capabilities"],
            limits=descriptors["limits"],
            promotion_id=f"promotion-revision-{promoted_revision}",
            adapter_id=snapshot.adapter_id,
            adapter_sha256=snapshot.adapter_sha256,
            base_model_id=snapshot.base_model_id,
            base_model_sha256=snapshot.base_model_sha256,
            job_id=snapshot.job_id,
            attempt_id=snapshot.attempt_id,
            fencing_token_digest=snapshot.fencing_token_digest,
            reason_sha256=hashlib.sha256(b"attested runtime handoff").hexdigest(),
        )
    )
    task_id = handoff_service.submit(
        handoff_plan,
        confirmation_digest=handoff_plan.confirmation_digest,
    )
    invocation = ModelInvocationService.resolve_runtime_handoff_endpoint(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        required_capability="openai_chat",
        expected_endpoint_revision=2,
        endpoint_registry=endpoints,
    )
    inference_contract_sha256 = _canonical_sha256(
        {
            "endpoint_revision": invocation["endpoint_revision"],
            "required_capability": invocation["required_capability"],
            "evaluation_manifest_sha256": evaluation_manifest_sha256,
            "max_output_tokens": invocation["limits"]["max_output_tokens"],
        }
    )
    observed_rejections["runtime_to_rollback"] = _observe_rejection(
        lambda: endpoints.rollback(
            tenant_id=snapshot.tenant_id,
            endpoint_id="nvidia-smoke-endpoint",
            expected_revision=1,
            reason_sha256=hashlib.sha256(
                b"runtime rollback tamper probe"
            ).hexdigest(),
            actor_id="lora-smoke-tamper-probe",
        )
    )
    rollback = endpoints.rollback(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        expected_revision=2,
        reason_sha256=hashlib.sha256(b"attested smoke rollback").hexdigest(),
        actor_id="nvidia-smoke-operator",
    )
    chain = {
        "dataset_to_training": dataset_sha256,
        "model_to_training": snapshot.base_model_sha256,
        "adapter_to_export": adapter_sha256,
        "export_to_evaluation": export_sha256,
        "evaluation_to_promotion": evaluation_manifest_sha256,
        "promotion_to_runtime": promotion_plan.confirmation_digest,
        "runtime_to_rollback": handoff_plan.confirmation_digest,
    }
    tamper_checks = _transition_tamper_checks(
        chain,
        observed_rejections=observed_rejections,
    )
    tamper_statuses = {
        item.get("status")
        for item in tamper_checks.values()
    }
    tamper_ok = all(
        item.get("status") == "passed"
        for item in tamper_checks.values()
    )
    return {
        **base,
        "adapter_evaluation": {
            "status": "passed",
            "job_id": evaluation_id,
            "attempt_id": evaluation_envelope["attempt_id"],
            "manifest_sha256": evaluation_manifest_sha256,
            "manifest_size_bytes": evaluation_manifest_path.stat().st_size,
        },
        "promotion": {
            "status": "passed",
            "registry_revision": promoted_revision,
            "plan_sha256": promotion_plan.confirmation_digest,
        },
        "runtime_load": {
            "status": "passed",
            "task_id": task_id,
            "endpoint_revision": invocation["endpoint_revision"],
            "required_capability": invocation["required_capability"],
            "inference_contract_sha256": inference_contract_sha256,
            "actual_adapter_load_evidence": evaluation_manifest_sha256,
            "implicit_fallback": False,
        },
        "rollback": {
            "status": "passed",
            "endpoint_revision": rollback.revision,
            "restored_from_revision": rollback.restored_from_revision,
            "promotion_call_count": len(promotion_port.calls),
        },
        "tamper_negative_paths": {
            "status": (
                "passed"
                if tamper_ok
                else "failed"
                if "failed" in tamper_statuses
                else "not_run"
            ),
            "transitions": tamper_checks,
        },
        "chain_sha256": _canonical_sha256(chain),
    }


def _write_jsonl(path: Path, records: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    return _file_sha256(path), len(records)


def _run_nvidia_live_smoke(
    model_path: Path,
    probe: Mapping[str, Any],
    *,
    backend: str = "peft_trl",
    target_modules: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    backend = _normalize_gpu_backend(backend)
    _reset_peak_vram()
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
        train_sha, train_count = _write_jsonl(dataset_root / "train.jsonl", fixed_train)
        validation_sha, validation_count = _write_jsonl(dataset_root / "validation.jsonl", fixed_validation)
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
            {backend: _nvidia_runtime_backend(backend)},
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
                    if (
                        len(exported_items) != int(manifest["file_count"])
                        or sum(int(artifact["size_bytes"]) for artifact in exported_items)
                        != int(manifest["total_bytes"])
                    ):
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
                "peak_vram": _peak_vram(),
                "backend": backend,
                "model_snapshot_sha256": probe["model_snapshot_sha256"],
                "dataset_sha256": hashlib.sha256(f"{train_sha}:{validation_sha}".encode()).hexdigest(),
                "configuration_sha256": hashlib.sha256(
                    json.dumps(envelope["configuration"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "library_fingerprint_sha256": _canonical_sha256(probe["packages"]),
                "gpu_fingerprint_sha256": _canonical_sha256(probe["gpu"]),
                "gpu": probe["gpu"],
                "packages": probe["packages"],
                "artifacts": evidence,
                "requested_exports": requested_exports,
            "platform_stage_coverage": _complete_unsloth_release_chain(
                runtime=runtime,
                training_envelope=envelope,
                workspace_root=workspace_root,
                root=root,
                probe=probe,
                export_evidence=export_evidence,
                timeout_seconds=timeout_seconds,
            ) if backend == "unsloth" else {
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


def _run_gate_legacy(
    *,
    run_mock: bool = True,
    nvidia_model: Path | None = None,
    require_nvidia: bool = False,
    gpu_backend: str = "peft_trl",
    src_ids: Sequence[str] = (),
    run_ids: Sequence[str] = (),
    runtime_image_digest: str | None = None,
    target_modules: Sequence[str] = ("q_proj", "k_proj", "v_proj", "o_proj"),
    timeout_seconds: float = 1800.0,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    gpu_backend = _normalize_gpu_backend(gpu_backend)
    evidence_ids = _normalize_evidence_ids(src_ids=src_ids, run_ids=run_ids)
    image_attestation = _worker_image_attestation(runtime_image_digest)
    versions = _installed_runtime_versions()
    mock = _mock_gate(runner) if run_mock else {"status": "not_run", "reason_code": "disabled_by_operator"}
    probe, admitted_model = _nvidia_probe(nvidia_model, runner, backend=gpu_backend)
    probe = {
        **probe,
        "evidence_ids": evidence_ids,
        "image_attestation": image_attestation,
        "versions": versions,
    }
    if admitted_model is None:
        nvidia = probe
    else:
        nvidia = _run_nvidia_live_smoke(
            admitted_model,
            probe,
            backend=gpu_backend,
            target_modules=target_modules,
            timeout_seconds=timeout_seconds,
        )
    mock_ok = mock["status"] in {"passed", "not_run"} and (not run_mock or mock["status"] == "passed")
    nvidia_required = require_nvidia or not run_mock
    nvidia_ok = nvidia["status"] == "passed" or (not nvidia_required and nvidia["status"] == "not_run")
    at_least_one_gate_passed = mock["status"] == "passed" or nvidia["status"] == "passed"
    support_claim = _support_claim(
        backend=gpu_backend,
        nvidia_result=nvidia,
        evidence_ids=evidence_ids,
        image_attestation=image_attestation,
        versions=versions,
    )
    return {
        "schema": "ananta.lora-training-control-center-gate.v1",
        "ok": bool(mock_ok and nvidia_ok and at_least_one_gate_passed),
        "profile": {"backend": gpu_backend},
        "evidence_ids": evidence_ids,
        "image_attestation": image_attestation,
        "versions": versions,
        "unsloth_support_claim": support_claim,
        "mock_cpu_gate": mock,
        "nvidia_live_smoke": nvidia,
        "nvidia_live_proof": nvidia["status"] == "passed",
        "privacy": {
            "synthetic_dataset_only": True,
            "raw_training_records_in_report": False,
            "credentials_in_report": False,
        },
        "reproduce": [
            _python_executable(),
            "scripts/run_lora_training_smoke.py",
            "--nvidia-model",
            "<LOCAL_MODEL_DIRECTORY>",
        ],
    }


def _aggregate_nvidia_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    compatibility_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    required_stages = (
        "training",
        "export",
        "training_evaluation",
        "adapter_evaluation",
        "promotion",
        "runtime_load",
        "rollback",
        "tamper_negative_paths",
    )
    run_attestations: list[dict[str, Any]] = []
    stage_coverage: dict[str, Any] = {}
    for stage in required_stages:
        stage_results = [
            dict(run.get("platform_stage_coverage", {}).get(stage) or {})
            for run in runs
        ]
        passed = bool(stage_results) and all(
            result.get("status") == "passed" for result in stage_results
        )
        stage_coverage[stage] = {
            "status": "passed" if passed else "failed",
            "run_statuses": [result.get("status", "missing") for result in stage_results],
        }
    for index, run in enumerate(runs, start=1):
        coverage = dict(run.get("platform_stage_coverage") or {})
        digest_payload = {
            "run_index": index,
            "status": run.get("status"),
            "platform_stage_coverage": coverage,
            "dataset_sha256": run.get("dataset_sha256"),
            "base_model_sha256": run.get("base_model_sha256"),
            "export_evidence": run.get("export_evidence"),
        }
        run_attestations.append(
            {
                "run_index": index,
                "status": run.get("status", "failed"),
                "chain_sha256": coverage.get("chain_sha256"),
                "attestation_sha256": hashlib.sha256(
                    json.dumps(
                        digest_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    passed_runs = sum(run.get("status") == "passed" for run in runs)
    all_passed = (
        len(runs) == _REQUIRED_UNSLOTH_RUNS
        and passed_runs == _REQUIRED_UNSLOTH_RUNS
        and compatibility_attestation.get("status") == "passed"
        and all(stage.get("status") == "passed" for stage in stage_coverage.values())
    )
    any_failed = any(run.get("status") == "failed" for run in runs)
    status = "passed" if all_passed else ("failed" if any_failed else "not_run")
    result: dict[str, Any] = {
        "status": status,
        "backend": "unsloth",
        "deterministic_run_count": passed_runs,
        "required_deterministic_runs": _REQUIRED_UNSLOTH_RUNS,
        "compatibility_attestation": dict(compatibility_attestation),
        "platform_stage_coverage": stage_coverage,
        "runs": run_attestations,
    }
    if not all_passed:
        result["reason_code"] = (
            "unsloth_gpu_run_failed"
            if any_failed
            else "deterministic_run_count_incomplete"
        )
    return result


def run_gate(
    *,
    run_mock: bool = True,
    nvidia_model: Path | None = None,
    require_nvidia: bool = False,
    gpu_backend: str = "peft_trl",
    src_ids: Sequence[str] = (),
    run_ids: Sequence[str] = (),
    runtime_image_digest: str | None = None,
    compatibility_matrix: Path | None = None,
    compatibility_entry: str | None = None,
    repeat_count: int = 1,
    target_modules: Sequence[str] = ("q_proj", "k_proj", "v_proj", "o_proj"),
    timeout_seconds: float = 1800.0,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    gpu_backend = _normalize_gpu_backend(gpu_backend)
    if gpu_backend != "unsloth":
        return _run_gate_legacy(
            run_mock=run_mock,
            nvidia_model=nvidia_model,
            require_nvidia=require_nvidia,
            gpu_backend=gpu_backend,
            src_ids=src_ids,
            run_ids=run_ids,
            runtime_image_digest=runtime_image_digest,
            target_modules=target_modules,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )

    evidence_ids = _normalize_evidence_ids(src_ids=src_ids, run_ids=run_ids)
    image_attestation = _worker_image_attestation(runtime_image_digest)
    versions = _installed_runtime_versions()
    selection = _compatibility_matrix_entry(
        compatibility_matrix,
        compatibility_entry,
    )
    probe, admitted_model = _nvidia_probe(
        nvidia_model,
        runner,
        backend=gpu_backend,
    )
    probe = {
        **probe,
        "model_basename": admitted_model.name if admitted_model is not None else None,
        "evidence_ids": evidence_ids,
        "image_attestation": image_attestation,
        "versions": versions,
    }
    pre_attestation = _compatibility_attestation(
        selection,
        probe=probe,
        versions=versions,
        image_attestation=image_attestation,
        required_runs=repeat_count,
    )
    configured = (
        repeat_count == _REQUIRED_UNSLOTH_RUNS
        and evidence_ids.get("complete") is True
        and admitted_model is not None
        and pre_attestation.get("status") == "passed"
    )
    runs: list[dict[str, Any]] = []
    base_report: dict[str, Any] | None = None
    if configured:
        for run_index in range(repeat_count):
            current = _run_gate_legacy(
                run_mock=run_mock if run_index == 0 else False,
                nvidia_model=admitted_model,
                require_nvidia=True,
                gpu_backend=gpu_backend,
                src_ids=src_ids,
                run_ids=run_ids,
                runtime_image_digest=runtime_image_digest,
                target_modules=target_modules,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            if base_report is None:
                base_report = dict(current)
            runs.append(dict(current["nvidia_live_smoke"]))
    else:
        base_report = _run_gate_legacy(
            run_mock=run_mock,
            nvidia_model=None,
            require_nvidia=False,
            gpu_backend=gpu_backend,
            src_ids=src_ids,
            run_ids=run_ids,
            runtime_image_digest=runtime_image_digest,
            target_modules=target_modules,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )

    completed_runs = sum(run.get("status") == "passed" for run in runs)
    compatibility_attestation = _compatibility_attestation(
        selection,
        probe=probe,
        versions=versions,
        image_attestation=image_attestation,
        required_runs=repeat_count,
        completed_runs=completed_runs,
    )
    if configured:
        nvidia = _aggregate_nvidia_runs(
            runs,
            compatibility_attestation=compatibility_attestation,
        )
    else:
        reason_code = (
            "deterministic_repeat_count_invalid"
            if repeat_count != _REQUIRED_UNSLOTH_RUNS
            else "source_or_run_ids_missing"
            if evidence_ids.get("complete") is not True
            else str(probe.get("reason_code") or "")
            if admitted_model is None
            else str(
                compatibility_attestation.get("reason_code")
                or "compatibility_profile_not_attested"
            )
        )
        prerequisite_failed = (
            selection.get("status") == "failed"
            or compatibility_attestation.get("status") == "failed"
        )
        nvidia = {
            **probe,
            "status": "failed" if prerequisite_failed else "not_run",
            "reason_code": reason_code,
            "deterministic_run_count": 0,
            "required_deterministic_runs": _REQUIRED_UNSLOTH_RUNS,
            "compatibility_attestation": compatibility_attestation,
            "platform_stage_coverage": {
                stage: {"status": "not_run", "reason_code": reason_code}
                for stage in (
                    "training",
                    "export",
                    "training_evaluation",
                    "adapter_evaluation",
                    "promotion",
                    "runtime_load",
                    "rollback",
                    "tamper_negative_paths",
                )
            },
            "runs": [],
        }

    assert base_report is not None
    mock = base_report["mock_cpu_gate"]
    mock_ok = (
        mock["status"] in {"passed", "not_run"}
        and (not run_mock or mock["status"] == "passed")
    )
    nvidia_required = require_nvidia or not run_mock
    nvidia_ok = (
        nvidia["status"] == "passed"
        or (not nvidia_required and nvidia["status"] == "not_run")
    )
    support_claim = _support_claim(
        backend=gpu_backend,
        nvidia_result=nvidia,
        evidence_ids=evidence_ids,
        image_attestation=image_attestation,
        versions=versions,
    )
    base_report.update(
        {
            "schema": "ananta.lora-training-smoke.v2",
            "ok": bool(
                mock_ok
                and nvidia_ok
                and (mock["status"] == "passed" or nvidia["status"] == "passed")
            ),
            "nvidia_probe": probe,
            "nvidia_live_smoke": nvidia,
            "nvidia_live_proof": nvidia["status"] == "passed",
            "compatibility_matrix": {
                "entry_id": compatibility_entry,
                "matrix_sha256": selection.get("matrix_sha256"),
                "status": selection.get("status"),
            },
            "unsloth_support_claim": support_claim,
        }
    )
    return base_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local LoRA training acceptance gate.")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--skip-mock", action="store_true")
    parser.add_argument(
        "--nvidia-model",
        default=os.environ.get("ANANTA_LORA_TRAINING_SMOKE_MODEL_DIR"),
        help="Local model directory; no model is downloaded.",
    )
    parser.add_argument("--require-nvidia", action="store_true")
    parser.add_argument(
        "--profile",
        choices=sorted(_GPU_BACKENDS),
        default=os.environ.get("ANANTA_LORA_TRAINING_SMOKE_PROFILE", "peft_trl"),
        help="GPU backend profile; use unsloth for an Unsloth support claim.",
    )
    parser.add_argument(
        "--src-id",
        action="append",
        default=[os.environ["ANANTA_UNSLOTH_SRC_IDS"]]
        if os.environ.get("ANANTA_UNSLOTH_SRC_IDS")
        else [],
        help="Externally supplied SRC_* evidence ID; repeat or provide comma-separated IDs.",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[os.environ["ANANTA_UNSLOTH_RUN_IDS"]]
        if os.environ.get("ANANTA_UNSLOTH_RUN_IDS")
        else [],
        help="Externally supplied RUN_* evidence ID; repeat or provide comma-separated IDs.",
    )
    parser.add_argument(
        "--runtime-image-digest",
        default=os.environ.get("ANANTA_LORA_WORKER_IMAGE_SHA256"),
        help="Runtime image digest as sha256:<64 lowercase hex>; never inferred from build inputs.",
    )
    parser.add_argument(
        "--compatibility-matrix",
        default=os.environ.get(
            "ANANTA_UNSLOTH_COMPATIBILITY_MATRIX",
            str(DEFAULT_COMPATIBILITY_MATRIX),
        ),
        help="Versioned Unsloth GPU compatibility matrix.",
    )
    parser.add_argument(
        "--matrix-entry",
        default=os.environ.get("ANANTA_UNSLOTH_COMPATIBILITY_ENTRY"),
        help="Approved compatibility matrix entry ID.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=int(os.environ.get("ANANTA_UNSLOTH_REPEAT_COUNT", "1")),
        help="Independent deterministic GPU runs; an Unsloth claim requires exactly 3.",
    )
    parser.add_argument("--target-modules", default="q_proj,k_proj,v_proj,o_proj")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    modules = tuple(item.strip() for item in args.target_modules.split(",") if item.strip())
    if not modules:
        parser.error("--target-modules must contain at least one module")
    if not 30.0 <= args.timeout_seconds <= 86_400.0:
        parser.error("--timeout-seconds must be between 30 and 86400")
    if not 1 <= args.repeat <= _REQUIRED_UNSLOTH_RUNS:
        parser.error(f"--repeat must be between 1 and {_REQUIRED_UNSLOTH_RUNS}")
    report = run_gate(
        run_mock=not args.skip_mock,
        nvidia_model=Path(args.nvidia_model) if args.nvidia_model else None,
        require_nvidia=args.require_nvidia,
        gpu_backend=args.profile,
        src_ids=args.src_id,
        run_ids=args.run_id,
        runtime_image_digest=args.runtime_image_digest,
        compatibility_matrix=Path(args.compatibility_matrix)
        if args.compatibility_matrix
        else None,
        compatibility_entry=args.matrix_entry,
        repeat_count=args.repeat,
        target_modules=modules,
        timeout_seconds=args.timeout_seconds,
    )
    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"mock_cpu_gate={report['mock_cpu_gate']['status']}")
    print(f"nvidia_live_smoke={report['nvidia_live_smoke']['status']}")
    if report["nvidia_live_smoke"].get("reason_code"):
        print(f"nvidia_reason={report['nvidia_live_smoke']['reason_code']}")
    print(f"nvidia_live_proof={str(report['nvidia_live_proof']).lower()}")
    print(
        "unsloth_support_claim_verified="
        f"{str(report['unsloth_support_claim']['verified']).lower()}"
    )
    print(f"report={output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
