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
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = "artifacts/lora-training-control-center-gate.json"
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
    "tests/worker/test_lora_training_app.py",
    "tests/worker/test_lora_training_backends.py",
    "tests/worker/test_lora_training_deployment.py",
    "tests/worker/test_lora_training_runtime.py",
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


def _nvidia_probe(model_path: Path | None, runner: CommandRunner) -> tuple[dict[str, Any], Path | None]:
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
    }, resolved


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
    target_modules: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    from worker.training.backends.peft_trl import PeftTrlTrainingBackend
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
            "backend": "peft_trl",
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
            {"peft_trl": PeftTrlTrainingBackend()},
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
            metadata = {item["name"]: item for item in status.get("artifacts") or []}
            if not expected.issubset(metadata):
                return {"status": "failed", "reason_code": "nvidia_smoke_artifacts_missing"}
            evidence: dict[str, Any] = {}
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
            return {
                "status": "passed",
                "job_status": status["status"],
                "worker_image_sha256": probe["worker_image"]["sha256"],
                "worker_image_fingerprint_kind": probe["worker_image"]["kind"],
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
            }
        finally:
            runtime.close()


def run_gate(
    *,
    run_mock: bool = True,
    nvidia_model: Path | None = None,
    require_nvidia: bool = False,
    target_modules: Sequence[str] = ("q_proj", "k_proj", "v_proj", "o_proj"),
    timeout_seconds: float = 1800.0,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    mock = _mock_gate(runner) if run_mock else {"status": "not_run", "reason_code": "disabled_by_operator"}
    probe, admitted_model = _nvidia_probe(nvidia_model, runner)
    if admitted_model is None:
        nvidia = probe
    else:
        nvidia = _run_nvidia_live_smoke(
            admitted_model,
            probe,
            target_modules=target_modules,
            timeout_seconds=timeout_seconds,
        )
    mock_ok = mock["status"] in {"passed", "not_run"} and (not run_mock or mock["status"] == "passed")
    nvidia_required = require_nvidia or not run_mock
    nvidia_ok = nvidia["status"] == "passed" or (not nvidia_required and nvidia["status"] == "not_run")
    at_least_one_gate_passed = mock["status"] == "passed" or nvidia["status"] == "passed"
    return {
        "schema": "ananta.lora-training-control-center-gate.v1",
        "ok": bool(mock_ok and nvidia_ok and at_least_one_gate_passed),
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
    parser.add_argument("--target-modules", default="q_proj,k_proj,v_proj,o_proj")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    modules = tuple(item.strip() for item in args.target_modules.split(",") if item.strip())
    if not modules:
        parser.error("--target-modules must contain at least one module")
    if not 30.0 <= args.timeout_seconds <= 86_400.0:
        parser.error("--timeout-seconds must be between 30 and 86400")
    report = run_gate(
        run_mock=not args.skip_mock,
        nvidia_model=Path(args.nvidia_model) if args.nvidia_model else None,
        require_nvidia=args.require_nvidia,
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
    print(f"report={output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
