#!/usr/bin/env python3
"""Run real Unsloth crash/resume/cancel scenarios under Hub evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from scripts.run_hub_evidence_unsloth_gpu_gate import (
    DEFAULT_IMAGE,
    DEFAULT_MODEL,
    ROOT,
    bounded_diagnostic,
    docker_image_id,
    docker_image_revision,
    nvidia_environment,
    repository_manifest,
    repository_revision,
    resolve_nvidia_libraries,
    tree_sha256,
)

TASK_ID = "UNSLOTH-GPU-RESILIENCE-GATE"
_TERMINAL_CONTAINER_STATES = frozenset({"exited", "dead", "removing"})


class UnslothGpuResilienceGateError(RuntimeError):
    """Bounded resilience gate failure."""


def container_command(
    *,
    name: str,
    image_id: str,
    model_path: Path,
    output_dir: Path,
    phase: str,
    timeout_seconds: int,
    root: Path = ROOT,
    libraries: Mapping[str, Path] | None = None,
    device_paths: Sequence[Path] | None = None,
    nvidia_smi_path: Path | None = None,
) -> list[str]:
    if re.fullmatch(r"ananta-unsloth-resilience-[0-9a-f]{16}-(source|resume)", name) is None:
        raise UnslothGpuResilienceGateError("unsloth_resilience_container_name_invalid")
    if phase not in {"source", "resume"}:
        raise UnslothGpuResilienceGateError("unsloth_resilience_phase_invalid")
    command = [
        "docker",
        "run",
        "--name",
        name,
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
    for device in device_paths or tuple(
        Path(value)
        for value in (
            "/dev/nvidia0",
            "/dev/nvidiactl",
            "/dev/nvidia-uvm",
            "/dev/nvidia-uvm-tools",
        )
    ):
        if not device.exists():
            raise UnslothGpuResilienceGateError("unsloth_resilience_gpu_device_missing")
        command.extend(("--device", str(device)))
    executable = str(nvidia_smi_path or shutil.which("nvidia-smi") or "").strip()
    if not executable:
        raise UnslothGpuResilienceGateError("unsloth_resilience_nvidia_smi_missing")
    command.extend(("--volume", f"{Path(executable).resolve(strict=True)}:/usr/bin/nvidia-smi:ro"))
    for library_name, library_path in (libraries or resolve_nvidia_libraries()).items():
        command.extend(("--volume", f"{library_path}:/host-nvidia/{library_name}:ro"))
        if library_name == "libcuda.so.1":
            command.extend(("--volume", f"{library_path}:/host-nvidia/libcuda.so:ro"))
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
            "TRANSFORMERS_OFFLINE=1",
            "--env",
            "TRITON_CACHE_DIR=/tmp/triton-cache",
            "--env",
            "CUDA_CACHE_PATH=/tmp/cuda-cache",
            "--env",
            "NUMBA_CACHE_DIR=/tmp/numba-cache",
            "--env",
            "UNSLOTH_COMPILE_LOCATION=/tmp/unsloth-compiled-cache",
            image_id,
            "python",
            "/gate/scripts/lora_training_resilience_live.py",
            "--phase",
            phase,
            "--root",
            "/output",
            "--model",
            f"/models/{model_path.name}",
            "--timeout-seconds",
            str(timeout_seconds),
        )
    )
    return command


def remove_container(name: str) -> None:
    subprocess.run(
        ("docker", "rm", "--force", name),
        capture_output=True,
        check=False,
        timeout=30,
    )


def container_state(name: str) -> tuple[str, int]:
    completed = subprocess.run(
        (
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.ExitCode}}",
            name,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    fields = completed.stdout.strip().split()
    if completed.returncode or len(fields) != 2 or not fields[1].isdigit():
        raise UnslothGpuResilienceGateError("unsloth_resilience_container_state_invalid")
    return fields[0], int(fields[1])


def wait_for_checkpoint_or_failure(name: str, output_dir: Path, timeout_seconds: int) -> Path:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        manifests = sorted((output_dir / "state").rglob("ananta-checkpoint-manifest.json"))
        if manifests:
            return manifests[0]
        state, _exit_code = container_state(name)
        if state in _TERMINAL_CONTAINER_STATES:
            raise UnslothGpuResilienceGateError("unsloth_resilience_source_exited_before_checkpoint")
        time.sleep(0.1)
    raise UnslothGpuResilienceGateError("unsloth_resilience_checkpoint_timeout")


def run_scenarios(
    *,
    image_id: str,
    model_path: Path,
    timeout_seconds: int,
    nonce: str,
) -> dict[str, Any]:
    source_name = f"ananta-unsloth-resilience-{nonce}-source"
    resume_name = f"ananta-unsloth-resilience-{nonce}-resume"
    with tempfile.TemporaryDirectory(prefix="ananta-unsloth-resilience-", ignore_cleanup_errors=True) as temporary:
        output_dir = Path(temporary)
        output_dir.chmod(0o777)
        source_command = container_command(
            name=source_name,
            image_id=image_id,
            model_path=model_path,
            output_dir=output_dir,
            phase="source",
            timeout_seconds=timeout_seconds,
        )
        source = subprocess.run(
            source_command[:2] + ["--detach"] + source_command[2:],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        checkpoint: Path | None = None
        source_exit = -1
        resume: subprocess.CompletedProcess[str] | None = None
        try:
            if source.returncode:
                raise UnslothGpuResilienceGateError("unsloth_resilience_source_start_failed")
            checkpoint = wait_for_checkpoint_or_failure(source_name, output_dir, timeout_seconds)
            killed = subprocess.run(
                ("docker", "kill", "--signal", "KILL", source_name),
                capture_output=True,
                check=False,
                timeout=30,
            )
            if killed.returncode:
                raise UnslothGpuResilienceGateError("unsloth_resilience_crash_injection_failed")
            _state, source_exit = container_state(source_name)
            remove_container(source_name)
            resume = subprocess.run(
                container_command(
                    name=resume_name,
                    image_id=image_id,
                    model_path=model_path,
                    output_dir=output_dir,
                    phase="resume",
                    timeout_seconds=timeout_seconds,
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds * 2,
            )
            report_path = output_dir / "resilience-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            return {
                "passed": bool(
                    source_exit == 137
                    and checkpoint is not None
                    and resume.returncode == 0
                    and report.get("status") == "passed"
                    and (report.get("fencing") or {}).get("stale_fence_rejected") is True
                    and (report.get("resource_release") or {}).get("released") is True
                ),
                "source_container_exit_code": source_exit,
                "crash_checkpoint": checkpoint.parent.name if checkpoint else None,
                "resume_container_returncode": resume.returncode,
                "scenarios": report,
                "stdout_digest": hashlib.sha256(resume.stdout.encode()).hexdigest(),
                "stderr_digest": hashlib.sha256(resume.stderr.encode()).hexdigest(),
                "stderr_tail": bounded_diagnostic(resume.stderr),
            }
        finally:
            remove_container(source_name)
            remove_container(resume_name)


def execute_gate(
    *,
    image: str,
    model_path: Path,
    output_path: Path,
    database_url: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], int]:
    revision = repository_revision()
    image_id = docker_image_id(image)
    if docker_image_revision(image) != revision:
        raise UnslothGpuResilienceGateError("unsloth_resilience_image_revision_mismatch")
    model_digest = tree_sha256(model_path)
    manifest = repository_manifest()
    environment = nvidia_environment()
    profile = {
        "schema": "ananta.unsloth-gpu-resilience-profile.v1",
        "network": "none",
        "worker_image": image_id,
        "real_gpu_required": True,
        "scenarios": ["hard_crash", "resume", "stale_fence", "forced_cancel", "resource_release"],
    }
    matrix_digest = canonical_evidence_digest(profile)
    engine = create_engine(database_url)
    SQLModel.metadata.create_all(
        engine,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    nonce = uuid.uuid4().hex
    request = EvidenceGateRequest(
        tenant_id="ananta-local",
        project_id="unsloth-gpu-resilience",
        task_id=TASK_ID,
        assignment_id=f"unsloth-resilience-assignment-{nonce}",
        dispatch_lease_id=f"unsloth-resilience-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {"repository": manifest["digest"], "model": model_digest, "worker_image": image_id}
        ),
        execution_profile_digest=matrix_digest,
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="local",
        required_scope="local",
        idempotency_key=f"unsloth-resilience:{revision}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission("repository_bundle", manifest["digest"], manifest["digest"], matrix_digest),
            EvidenceGateSourceAdmission("model_snapshot", model_digest, model_digest, matrix_digest),
            EvidenceGateSourceAdmission(
                "worker_image", image_id.removeprefix("sha256:"), image_id.removeprefix("sha256:"), matrix_digest
            ),
        ),
    )

    def worker(_assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        execution = run_scenarios(
            image_id=image_id,
            model_path=model_path,
            timeout_seconds=timeout_seconds,
            nonce=nonce[:16],
        )
        immutable = bool(
            repository_manifest()["digest"] == manifest["digest"]
            and tree_sha256(model_path) == model_digest
            and docker_image_id(image) == image_id
            and docker_image_revision(image) == revision
        )
        return {
            **execution,
            "passed": bool(execution["passed"] and immutable),
            "reason_code": (
                "unsloth_gpu_resilience_gate_passed"
                if execution["passed"] and immutable
                else "unsloth_gpu_resilience_gate_failed"
            ),
            "immutable_inputs_unchanged": immutable,
        }

    outcome = HubEvidenceGateService(registry).execute(request, worker)
    report = {
        "schema": "ananta.hub-evidence-unsloth-gpu-resilience-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "local",
        "verified": outcome.verified,
        "execution_profile": profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/unsloth-gpu-resilience-evidence.json",
    )
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data/hub-evidence-unsloth.sqlite3'}",
    )
    args = parser.parse_args()
    if not 60 <= args.timeout_seconds <= 3600:
        raise UnslothGpuResilienceGateError("unsloth_resilience_timeout_invalid")
    report, returncode = execute_gate(
        image=args.image,
        model_path=args.model,
        output_path=args.output,
        database_url=args.database_url,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({"run_id": report["run_id"], "status": report["status"]}, sort_keys=True))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
