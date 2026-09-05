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
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)
from scripts.lora_training_smoke_compatibility import (
    aggregate_nvidia_runs as _aggregate_nvidia_runs,
)
from scripts.lora_training_smoke_compatibility import (
    compatibility_attestation as _compatibility_attestation,
)
from scripts.lora_training_smoke_compatibility import (
    compatibility_matrix_entry as _compatibility_matrix_entry,
)
from scripts.lora_training_smoke_files import (
    file_sha256 as _file_sha256,
)
from scripts.lora_training_smoke_files import (
    tree_sha256 as _tree_sha256,
)
from scripts.lora_training_smoke_live import (
    normalize_gpu_backend as _normalize_gpu_backend,
)
from scripts.lora_training_smoke_live import (
    nvidia_runtime_backend as _nvidia_runtime_backend,
)
from scripts.lora_training_smoke_live import (
    run_nvidia_live_smoke as _run_nvidia_live_smoke,
)
from scripts.lora_training_smoke_release_chain import (
    transition_tamper_checks as _transition_tamper_checks,
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
_PACKAGE_NAMES = (
    "torch",
    "torchao",
    "transformers",
    "datasets",
    "peft",
    "trl",
    "safetensors",
    "bitsandbytes",
)
_SAFE_MODEL_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
_MOCK_GATE_SUITE_ORDER = (
    "hub_control",
    "hub_unsloth",
    "worker_control",
    "worker_unsloth",
    "control_plane_e2e",
)

__all__ = [
    "_aggregate_nvidia_runs",
    "_compatibility_attestation",
    "_compatibility_matrix_entry",
    "_normalize_evidence_ids",
    "_normalize_gpu_backend",
    "_nvidia_probe",
    "_nvidia_runtime_backend",
    "_support_claim",
    "_transition_tamper_checks",
    "_tree_sha256",
    "_worker_image_attestation",
    "_worker_image_build_input_paths",
    "_worker_image_fingerprint",
    "run_gate",
]


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


def _suite_sha256(paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(path).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _mock_gate_suite_name(path: str) -> str:
    if path.startswith("tests/e2e/"):
        return "control_plane_e2e"
    if path.startswith("tests/worker/"):
        return "worker_unsloth" if "unsloth" in path else "worker_control"
    return "hub_unsloth" if "unsloth" in path else "hub_control"


def _mock_gate_suites() -> tuple[tuple[str, tuple[str, ...]], ...]:
    grouped = {
        name: tuple(path for path in MOCK_GATE_TESTS if _mock_gate_suite_name(path) == name)
        for name in _MOCK_GATE_SUITE_ORDER
    }
    if any(not paths for paths in grouped.values()) or sum(map(len, grouped.values())) != len(MOCK_GATE_TESTS):
        raise ValueError("lora_mock_gate_suite_partition_invalid")
    return tuple((name, grouped[name]) for name in _MOCK_GATE_SUITE_ORDER)


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
    legacy_command = [_python_executable(), "-m", "pytest", "-q", *MOCK_GATE_TESTS]
    suite_reports: list[dict[str, Any]] = []
    isolated_commands: list[list[str]] = []
    for name, paths in _mock_gate_suites():
        command = [_python_executable(), "-m", "pytest", "-q", *paths]
        isolated_commands.append(command)
        result = runner(command)
        combined = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(?P<count>\d+) passed", combined)
        reason_code = (
            "lora_mock_gate_suite_passed"
            if result.returncode == 0
            else f"lora_mock_gate_suite_signal:{-result.returncode}"
            if result.returncode < 0
            else f"lora_mock_gate_suite_exit:{result.returncode}"
        )
        suite_reports.append(
            {
                "name": name,
                "status": "passed" if result.returncode == 0 else "failed",
                "returncode": int(result.returncode),
                "reason_code": reason_code,
                "tests_passed": int(match.group("count")) if match else None,
                "suite_sha256": _suite_sha256(paths),
            }
        )
    status = "passed" if all(item["status"] == "passed" for item in suite_reports) else "failed"
    counts = [item["tests_passed"] for item in suite_reports]
    return {
        "status": status,
        "returncode": 0 if status == "passed" else 1,
        "tests_passed": sum(counts) if all(isinstance(count, int) for count in counts) else None,
        "suite_sha256": _suite_sha256(MOCK_GATE_TESTS),
        "worker_image": _worker_image_fingerprint(),
        "capabilities_proven": sorted(_MOCK_CAPABILITY_EVIDENCE) if status == "passed" else [],
        "capability_evidence": {
            capability: list(paths) for capability, paths in sorted(_MOCK_CAPABILITY_EVIDENCE.items())
        },
        "reproduce": list(legacy_command),
        "isolated_reproduce": isolated_commands,
        "suites": suite_reports,
    }


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


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
        "torchao",
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
