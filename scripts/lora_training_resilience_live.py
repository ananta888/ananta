"""Worker-side phases for the real NVIDIA crash/resume resilience gate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from scripts.lora_training_smoke_files import tree_sha256
from scripts.lora_training_smoke_release_chain import write_jsonl
from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.runtime import (
    RuntimeConfiguration,
    TrainingRuntimeError,
    TrainingWorkerRuntime,
)

JOB_ID = "nvidia-resilience-training"
SOURCE_ATTEMPT = "nvidia-resilience-attempt-1"
RESUME_ATTEMPT = "nvidia-resilience-attempt-2"
CANCEL_JOB_ID = "nvidia-resilience-cancel"
TENANT_SCOPE = hashlib.sha256(b"ananta-nvidia-resilience-tenant-v1").hexdigest()
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def build_training_envelope(
    *,
    model_path: Path,
    dataset_root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    train_rows = tuple(
        {"instruction": f"Return token {token}.", "output": token} for token in ("alpha", "beta", "gamma", "delta")
    )
    validation_rows = tuple({"instruction": f"Return token {token}.", "output": token} for token in ("epsilon", "zeta"))
    train_sha, train_count = write_jsonl(dataset_root / "train.jsonl", train_rows)
    validation_sha, validation_count = write_jsonl(dataset_root / "validation.jsonl", validation_rows)
    return {
        "contract_version": "ananta.lora-training.v1",
        "job_id": JOB_ID,
        "attempt_id": SOURCE_ATTEMPT,
        "fencing_token": 1,
        "correlation_id": "nvidia-resilience-correlation",
        "job_type": "train_lora",
        "backend": "unsloth",
        "resource_profile": "nvidia",
        "tenant_scope_digest": TENANT_SCOPE,
        "workspace_ref": "resilience",
        "deadline_epoch_ms": int((time.time() + timeout_seconds) * 1000),
        "base_model": {
            "model_id": "local/nvidia-resilience-model",
            "relative_path": model_path.name,
            "snapshot_hash": tree_sha256(model_path),
        },
        "dataset": {
            "dataset_id": "nvidia-resilience-dataset",
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
            "max_steps": 100,
            "num_train_epochs": 100.0,
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
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
        },
    }


def visible_gpu_processes() -> dict[int, int]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode:
        raise RuntimeError("nvidia_resilience_process_probe_failed")
    processes: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            processes[int(fields[0])] = int(fields[1])
    return processes


def runtime_for(root: Path, model_path: Path) -> TrainingWorkerRuntime:
    return TrainingWorkerRuntime(
        RuntimeConfiguration(
            state_root=root / "state",
            workspace_root=root / "workspaces",
            dataset_root=root / "datasets",
            model_root=model_path.parent,
            resource_profile="nvidia",
            max_workers=1,
            max_queue=0,
            max_dataset_bytes=16 * 1024 * 1024,
            max_dataset_records=100,
            isolate_processes=True,
            termination_grace_seconds=2.0,
        ),
        {"unsloth": UnslothTrainingBackend()},
    )


def wait_for_status(
    runtime: TrainingWorkerRuntime,
    job_id: str,
    *,
    timeout_seconds: float,
    predicate: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    status = runtime.status(job_id)
    while not predicate(status) and time.monotonic() < deadline:
        time.sleep(0.1)
        status = runtime.status(job_id)
    if not predicate(status):
        raise RuntimeError(f"nvidia_resilience_timeout:{job_id}:{status['status']}")
    return status


def run_source(root: Path, model_path: Path, timeout_seconds: float) -> None:
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "workspaces" / "resilience").mkdir(parents=True, exist_ok=True)
    envelope = build_training_envelope(
        model_path=model_path,
        dataset_root=root / "datasets",
        timeout_seconds=timeout_seconds,
    )
    (root / "source-request.json").write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    runtime = runtime_for(root, model_path)
    runtime.submit(envelope)
    status = wait_for_status(
        runtime,
        JOB_ID,
        timeout_seconds=timeout_seconds,
        predicate=lambda value: value.get("resume_checkpoint") is not None or value.get("status") in TERMINAL,
    )
    if status.get("resume_checkpoint") is None:
        raise RuntimeError(f"nvidia_resilience_checkpoint_missing:{status['status']}")
    (root / "checkpoint-ready.json").write_text(
        json.dumps(status, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    # The Hub-side gate kills this container after observing the durable marker.
    while True:
        time.sleep(1)


def retry_envelope(source: Mapping[str, Any], recovered: Mapping[str, Any]) -> dict[str, Any]:
    retry = copy.deepcopy(dict(source))
    retry["attempt_id"] = RESUME_ATTEMPT
    retry["fencing_token"] = 2
    retry["deadline_epoch_ms"] = int((time.time() + 300) * 1000)
    retry["resume_checkpoint"] = copy.deepcopy(recovered["resume_checkpoint"])
    return retry


def cancel_envelope(source: Mapping[str, Any]) -> dict[str, Any]:
    envelope = copy.deepcopy(dict(source))
    envelope["job_id"] = CANCEL_JOB_ID
    envelope["attempt_id"] = "nvidia-resilience-cancel-attempt-1"
    envelope["correlation_id"] = "nvidia-resilience-cancel-correlation"
    envelope["deadline_epoch_ms"] = int((time.time() + 300) * 1000)
    envelope["configuration"]["max_steps"] = 100
    envelope["configuration"]["num_train_epochs"] = 100.0
    return envelope


def release_boundary(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o777 if path.is_dir() else 0o666)


def run_resume(root: Path, model_path: Path, timeout_seconds: float) -> dict[str, Any]:
    source = json.loads((root / "source-request.json").read_text(encoding="utf-8"))
    baseline_processes = visible_gpu_processes()
    runtime = runtime_for(root, model_path)
    try:
        recovered = runtime.status(JOB_ID)
        if (
            recovered["status"] != "failed"
            or (recovered.get("error") or {}).get("code") != "worker_restarted"
            or not recovered.get("resume_checkpoint")
        ):
            raise RuntimeError("nvidia_resilience_crash_recovery_invalid")
        retry = retry_envelope(source, recovered)
        runtime.submit(retry)
        stale = copy.deepcopy(retry)
        stale["attempt_id"] = "nvidia-resilience-stale-attempt"
        stale["fencing_token"] = 1
        stale_rejected = False
        try:
            runtime.submit(stale)
        except TrainingRuntimeError as exc:
            stale_rejected = exc.code == "stale_fence" and exc.http_status == 409
        resumed = wait_for_status(
            runtime,
            JOB_ID,
            timeout_seconds=timeout_seconds,
            predicate=lambda value: value.get("status") in TERMINAL,
        )
        if resumed["status"] != "succeeded" or not stale_rejected:
            raise RuntimeError("nvidia_resilience_resume_or_fence_failed")

        cancel_request = cancel_envelope(source)
        runtime.submit(cancel_request)
        active_processes: dict[int, int] = {}
        deadline = time.monotonic() + timeout_seconds
        cancel_status = runtime.status(CANCEL_JOB_ID)
        while time.monotonic() < deadline:
            cancel_status = runtime.status(CANCEL_JOB_ID)
            active_processes = {
                pid: memory for pid, memory in visible_gpu_processes().items() if pid not in baseline_processes
            }
            if cancel_status["status"] == "running" and active_processes:
                break
            if cancel_status["status"] in TERMINAL:
                break
            time.sleep(0.1)
        if cancel_status["status"] != "running" or not active_processes:
            raise RuntimeError("nvidia_resilience_cancel_gpu_process_missing")
        runtime.cancel(CANCEL_JOB_ID)
        cancelled = wait_for_status(
            runtime,
            CANCEL_JOB_ID,
            timeout_seconds=timeout_seconds,
            predicate=lambda value: value.get("status") in TERMINAL,
        )
        deadline = time.monotonic() + 15
        leaked = set(active_processes)
        while leaked and time.monotonic() < deadline:
            leaked = set(active_processes).intersection(visible_gpu_processes())
            if leaked:
                time.sleep(0.1)
        report = {
            "status": "passed" if cancelled["status"] == "cancelled" and not leaked else "failed",
            "crash": {
                "source_attempt": SOURCE_ATTEMPT,
                "checkpoint": recovered["resume_checkpoint"],
                "recovery_reason_code": "worker_restarted",
            },
            "resume": {
                "attempt_id": resumed["attempt_id"],
                "fencing_token": resumed["fencing_token"],
                "status": resumed["status"],
                "progress": resumed["progress"],
            },
            "fencing": {"stale_fence_rejected": stale_rejected},
            "cancellation": {
                "status": cancelled["status"],
                "cancel_mode": cancelled["cancel_mode"],
                "gpu_processes_observed": active_processes,
            },
            "resource_release": {
                "released": not leaked,
                "leaked_pids": sorted(leaked),
                "baseline_gpu_processes": baseline_processes,
            },
        }
        if report["status"] != "passed":
            raise RuntimeError("nvidia_resilience_cancel_or_release_failed")
        (root / "resilience-report.json").write_text(
            json.dumps(report, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        return report
    finally:
        runtime.close()
        release_boundary(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("source", "resume"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()
    if args.phase == "source":
        run_source(args.root, args.model, args.timeout_seconds)
        return 0
    report = run_resume(args.root, args.model, args.timeout_seconds)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
