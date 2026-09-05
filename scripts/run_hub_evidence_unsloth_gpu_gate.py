#!/usr/bin/env python3
"""Run the real Unsloth GPU profile under a Hub-issued evidence reservation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.db_models.evidence_identity import (  # noqa: E402
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository  # noqa: E402
from agent.services.hub_evidence_gate_service import (  # noqa: E402
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService  # noqa: E402

TASK_ID = "UNSLOTH-GPU-RELEASE-GATE"
DEFAULT_IMAGE = "ananta-lora-training-worker:local-nvidia"
DEFAULT_MODEL = ROOT / "data/gpu-models/tiny-causal-lm"
DEFAULT_MATRIX = ROOT / "docs/contracts/unsloth-gpu-compatibility-matrix.v1.json"
DEFAULT_MATRIX_ENTRY = "unsloth-2026.7.5-cu124-torch260-tiny-causal-lm"
SOURCE_PATHS = (
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "ananta_contracts/hub_evidence.py",
    "ananta_contracts/training_backend.py",
    "docker/compose-next/Dockerfile.lora-training-worker",
    "docker/compose-next/lora-training-worker-entrypoint.sh",
    "docker/compose-next/requirements.lora-training-runtime.txt",
    "docker/compose-next/requirements.lora-training-nvidia.txt",
    "docs/contracts/unsloth-gpu-compatibility-matrix.v1.json",
    "scripts/lora_training_smoke_live.py",
    "scripts/lora_training_smoke_compatibility.py",
    "scripts/lora_training_smoke_files.py",
    "scripts/lora_training_smoke_release_chain.py",
    "scripts/run_hub_evidence_unsloth_gpu_gate.py",
    "scripts/run_lora_training_smoke.py",
    "worker/runtime/lora_training_app.py",
    "worker/training/backends/unsloth.py",
    "worker/training/backends/unsloth_checkpoint.py",
    "worker/training/contracts.py",
    "worker/training/datasets.py",
    "worker/training/evaluation.py",
    "worker/training/exports.py",
    "worker/training/inference.py",
    "worker/training/job_process.py",
    "worker/training/model_imports.py",
    "worker/training/runtime.py",
    "worker/training/runtime_artifact_service.py",
    "worker/training/unsloth_task_handlers.py",
    "worker/training/unsloth_worker_runtime.py",
    "worker/training/vram_admission.py",
)
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_NVIDIA_LIBRARIES = (
    "libcuda.so.1",
    "libnvidia-ml.so.1",
    "libnvidia-ptxjitcompiler.so.1",
)
_NVIDIA_LINKER_ALIASES = {"libcuda.so.1": ("libcuda.so",)}
_DIAGNOSTIC_LIMIT = 2000


class UnslothGpuGateError(ValueError):
    """Bounded configuration or environment failure."""


def bounded_diagnostic(value: object) -> str:
    """Keep worker failures actionable without persisting unbounded process output."""
    normalized = "".join(
        character if character in "\n\t" or character.isprintable() else "?"
        for character in str(value or "")
    )
    return normalized[-_DIAGNOSTIC_LIMIT:]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise UnslothGpuGateError("unsloth_gate_model_path_invalid")
    entries: list[dict[str, str]] = []
    for candidate in sorted(resolved.rglob("*")):
        if candidate.is_symlink():
            raise UnslothGpuGateError("unsloth_gate_model_symlink_forbidden")
        if candidate.is_file():
            entries.append(
                {
                    "path": candidate.relative_to(resolved).as_posix(),
                    "sha256": sha256_file(candidate),
                }
            )
    if not entries:
        raise UnslothGpuGateError("unsloth_gate_model_empty")
    return canonical_evidence_digest(entries)


def repository_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [
        {"path": value, "sha256": sha256_file((root / value).resolve(strict=True))}
        for value in SOURCE_PATHS
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def repository_revision(root: Path = ROOT) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, capture_output=True, text=True, check=False
    )
    revision = completed.stdout.strip().lower()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise UnslothGpuGateError("unsloth_gate_repository_revision_invalid")
    changed = subprocess.run(
        ("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS), cwd=root, check=False
    )
    if changed.returncode != 0:
        raise UnslothGpuGateError("unsloth_gate_bound_sources_dirty")
    return revision


def docker_image_id(image: str) -> str:
    completed = subprocess.run(
        ("docker", "image", "inspect", "--format", "{{.Id}}", image),
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip().lower()
    if completed.returncode != 0 or _DIGEST.fullmatch(value) is None:
        raise UnslothGpuGateError("unsloth_gate_worker_image_unavailable")
    return value


def docker_image_revision(image: str) -> str:
    completed = subprocess.run(
        (
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            image,
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip().lower()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise UnslothGpuGateError("unsloth_gate_worker_image_revision_invalid")
    return value


def nvidia_environment() -> dict[str, Any]:
    completed = subprocess.run(
        (
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,uuid",
            "--format=csv,noheader,nounits",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    rows = []
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", maxsplit=3)]
        if len(fields) == 4:
            rows.append(
                {
                    "name": fields[0][:160],
                    "driver": fields[1][:64],
                    "memory_mib": int(fields[2]),
                    "uuid": fields[3][:96],
                }
            )
    if completed.returncode != 0 or not rows:
        raise UnslothGpuGateError("unsloth_gate_nvidia_device_unavailable")
    return {
        "schema": "ananta.unsloth-gpu-gate-environment.v1",
        "gpu": rows,
        "host": platform.node(),
        "machine": platform.machine().lower(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


def resolve_nvidia_libraries() -> dict[str, Path]:
    completed = subprocess.run(("ldconfig", "-p"), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise UnslothGpuGateError("unsloth_gate_nvidia_libraries_unavailable")
    resolved: dict[str, Path] = {}
    for name in _NVIDIA_LIBRARIES:
        for line in completed.stdout.splitlines():
            if line.strip().startswith(f"{name} ") and "=>" in line:
                candidate = Path(line.rsplit("=>", 1)[1].strip()).resolve(strict=True)
                resolved[name] = candidate
                break
    if set(resolved) != set(_NVIDIA_LIBRARIES):
        raise UnslothGpuGateError("unsloth_gate_nvidia_libraries_unavailable")
    return resolved


def build_container_command(
    *,
    image: str,
    image_id: str,
    model_path: Path,
    output_dir: Path,
    assignment: Mapping[str, Any],
    matrix_entry: str,
    timeout_seconds: int,
    root: Path = ROOT,
    libraries: Mapping[str, Path] | None = None,
    device_paths: Sequence[Path] | None = None,
    nvidia_smi_path: Path | None = None,
) -> list[str]:
    source_ids = assignment.get("source_ids")
    run_id = str(assignment.get("run_id") or "")
    if not isinstance(source_ids, list) or not source_ids or not run_id.startswith("RUN_"):
        raise UnslothGpuGateError("unsloth_gate_assignment_invalid")
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "512",
        "--cpus",
        "8",
        "--memory",
        "48g",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=4294967296,uid=10005,gid=10005",
    ]
    devices = device_paths or tuple(
        Path(value)
        for value in (
            "/dev/nvidia0",
            "/dev/nvidiactl",
            "/dev/nvidia-uvm",
            "/dev/nvidia-uvm-tools",
        )
    )
    for device in devices:
        if not device.exists():
            raise UnslothGpuGateError("unsloth_gate_nvidia_device_unavailable")
        command.extend(("--device", str(device)))
    executable = str(nvidia_smi_path or shutil.which("nvidia-smi") or "").strip()
    if not executable:
        raise UnslothGpuGateError("unsloth_gate_nvidia_device_unavailable")
    nvidia_smi = Path(executable).resolve(strict=True)
    command.extend(("--volume", f"{nvidia_smi}:/usr/bin/nvidia-smi:ro"))
    for name, path in (libraries or resolve_nvidia_libraries()).items():
        command.extend(("--volume", f"{path}:/host-nvidia/{name}:ro"))
        for alias in _NVIDIA_LINKER_ALIASES.get(name, ()):
            command.extend(("--volume", f"{path}:/host-nvidia/{alias}:ro"))
    command.extend(
        (
            "--volume",
            f"{root.resolve(strict=True)}:/gate:ro",
            "--volume",
            f"{model_path.resolve(strict=True)}:/models/{model_path.name}:ro",
            "--volume",
            f"{output_dir.resolve(strict=True)}:/output:rw",
            "--env",
            "LD_LIBRARY_PATH=/host-nvidia",
            "--env",
            "NVIDIA_VISIBLE_DEVICES=0",
            "--env",
            "PYTHONPATH=/app:/gate",
            "--env",
            "HF_DATASETS_OFFLINE=1",
            "--env",
            "HF_HUB_OFFLINE=1",
            "--env",
            "TRITON_CACHE_DIR=/tmp/triton-cache",
            "--env",
            "CUDA_CACHE_PATH=/tmp/cuda-cache",
            "--env",
            "NUMBA_CACHE_DIR=/tmp/numba-cache",
            "--env",
            "UNSLOTH_COMPILE_LOCATION=/tmp/unsloth-compiled-cache",
            "--env",
            "TRANSFORMERS_OFFLINE=1",
            "--env",
            f"ANANTA_UNSLOTH_SRC_IDS={','.join(str(value) for value in source_ids)}",
            "--env",
            f"ANANTA_UNSLOTH_RUN_IDS={run_id}",
            "--env",
            f"ANANTA_LORA_WORKER_IMAGE_SHA256={image_id}",
            image,
            "python",
            "/gate/scripts/run_lora_training_smoke.py",
            "--skip-mock",
            "--require-nvidia",
            "--profile",
            "unsloth",
            "--nvidia-model",
            f"/models/{model_path.name}",
            "--compatibility-matrix",
            "/gate/docs/contracts/unsloth-gpu-compatibility-matrix.v1.json",
            "--matrix-entry",
            matrix_entry,
            "--repeat",
            "3",
            "--timeout-seconds",
            str(timeout_seconds),
            "--out",
            "/output/unsloth-gpu-smoke.json",
        )
    )
    return command


def execute_gate(
    *,
    image: str,
    model_path: Path,
    matrix_path: Path,
    matrix_entry: str,
    output_path: Path,
    database_url: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], int]:
    if matrix_path.resolve(strict=True) != DEFAULT_MATRIX.resolve(strict=True):
        raise UnslothGpuGateError("unsloth_gate_matrix_path_invalid")
    revision = repository_revision()
    manifest = repository_manifest()
    model_digest = tree_sha256(model_path)
    image_id = docker_image_id(image)
    if docker_image_revision(image) != revision:
        raise UnslothGpuGateError("unsloth_gate_worker_image_revision_mismatch")
    matrix_digest = sha256_file(matrix_path.resolve(strict=True))
    environment = nvidia_environment()
    execution_profile = {
        "schema": "ananta.unsloth-gpu-gate-profile.v1",
        "image_id": image_id,
        "matrix_entry": matrix_entry,
        "model_digest": model_digest,
        "repeat": 3,
        "timeout_seconds": timeout_seconds,
    }
    nonce = uuid.uuid4().hex
    engine = create_engine(database_url)
    SQLModel.metadata.create_all(
        engine,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    request = EvidenceGateRequest(
        tenant_id="ananta-local",
        project_id="unsloth-gpu-release",
        task_id=TASK_ID,
        assignment_id=f"unsloth-assignment-{nonce}",
        dispatch_lease_id=f"unsloth-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {"repository": manifest["digest"], "model": model_digest, "image": image_id}
        ),
        execution_profile_digest=canonical_evidence_digest(execution_profile),
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="local",
        required_scope="local",
        idempotency_key=f"unsloth-gpu:{revision}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission("repository_bundle", manifest["digest"], manifest["digest"], matrix_digest),
            EvidenceGateSourceAdmission("model_snapshot", model_digest, model_digest, matrix_digest),
            EvidenceGateSourceAdmission(
                "worker_image",
                image_id.removeprefix("sha256:"),
                image_id.removeprefix("sha256:"),
                matrix_digest,
            ),
        ),
    )

    def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ananta-unsloth-gpu-") as temporary:
            output_dir = Path(temporary)
            output_dir.chmod(0o777)
            command = build_container_command(
                image=image_id,
                image_id=image_id,
                model_path=model_path,
                output_dir=output_dir,
                assignment=assignment,
                matrix_entry=matrix_entry,
                timeout_seconds=timeout_seconds,
            )
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds * 3 + 300,
                )
            except subprocess.TimeoutExpired as exc:
                return {
                    "passed": False,
                    "reason_code": "unsloth_gpu_container_timeout",
                    "stdout_digest": hashlib.sha256(str(exc.stdout or "").encode()).hexdigest(),
                    "stderr_digest": hashlib.sha256(str(exc.stderr or "").encode()).hexdigest(),
                    "stderr_tail": bounded_diagnostic(exc.stderr),
                }
            report_file = output_dir / "unsloth-gpu-smoke.json"
            report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.is_file() else {}
            smoke = dict(report.get("nvidia_live_smoke") or {})
            claim = dict(report.get("unsloth_support_claim") or {})
            expected_sources = sorted(str(value) for value in assignment["source_ids"])
            immutable_inputs_unchanged = bool(
                tree_sha256(model_path) == model_digest
                and repository_manifest()["digest"] == manifest["digest"]
                and docker_image_id(image) == image_id
                and docker_image_revision(image) == revision
            )
            passed = bool(
                completed.returncode == 0
                and report.get("ok") is True
                and smoke.get("status") == "passed"
                and smoke.get("deterministic_run_count") == 3
                and claim.get("verified") is True
                and sorted(claim.get("src_ids") or []) == expected_sources
                and claim.get("run_ids") == [assignment["run_id"]]
                and immutable_inputs_unchanged
            )
            return {
                "passed": passed,
                "reason_code": "unsloth_gpu_gate_passed" if passed else "unsloth_gpu_gate_failed",
                "container_returncode": completed.returncode,
                "image_id": image_id,
                "model_snapshot_sha256": model_digest,
                "deterministic_run_count": smoke.get("deterministic_run_count", 0),
                "compatibility_attestation": smoke.get("compatibility_attestation"),
                "gpu": smoke.get("gpu"),
                "packages": smoke.get("packages"),
                "platform_stage_coverage": smoke.get("platform_stage_coverage"),
                "run_attestation_sha256": [row.get("attestation_sha256") for row in smoke.get("runs") or []],
                "run_results": [
                    {
                        "run_index": row.get("run_index"),
                        "status": row.get("status"),
                        "reason_code": row.get("reason_code"),
                        "stage_results": row.get("stage_results"),
                    }
                    for row in smoke.get("runs") or []
                ],
                "immutable_inputs_unchanged": immutable_inputs_unchanged,
                "stdout_digest": hashlib.sha256(completed.stdout.encode()).hexdigest(),
                "stderr_digest": hashlib.sha256(completed.stderr.encode()).hexdigest(),
                "stderr_tail": bounded_diagnostic(completed.stderr),
            }

    outcome = HubEvidenceGateService(registry).execute(request, worker)
    report = {
        "schema": "ananta.hub-evidence-unsloth-gpu-gate-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "local",
        "verified": outcome.verified,
        "execution_profile": execution_profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--matrix-entry", default=DEFAULT_MATRIX_ENTRY)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/unsloth-gpu-release-evidence.json")
    parser.add_argument("--database-url", default=f"sqlite:///{ROOT / 'data/hub-evidence-unsloth.sqlite3'}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 30 <= args.timeout_seconds <= 28_800:
        raise UnslothGpuGateError("unsloth_gate_timeout_invalid")
    report, returncode = execute_gate(
        image=args.image,
        model_path=args.model,
        matrix_path=args.matrix,
        matrix_entry=args.matrix_entry,
        output_path=args.output,
        database_url=args.database_url,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({"status": report["status"], "run_id": report["run_id"]}, sort_keys=True))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
