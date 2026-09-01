"""Artifact, provenance, and quota responsibilities for training workers.

The mixin operates on an admitted runtime instance and never owns queues or
performs Hub orchestration.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingOutcome
from worker.training.contracts import (
    CONTRACT_VERSION,
    AdapterEvaluationJobRequest,
    TrainingContractError,
    TrainingJobRequest,
)
from worker.training.evaluation import AdapterEvaluationOutcome

RESOURCE_ADMISSION_PAYLOAD_KEYS = frozenset(
    {
        "profile",
        "admitted",
        "estimated_peak_bytes",
        "usable_bytes",
        "reserve_bytes",
        "assumptions",
        "estimate_only",
        "reason_code",
    }
)


class TrainingRuntimeArtifactMixin:
    def _admit_artifacts(
        self,
        job: Any,
        outcome: TrainingOutcome | AdapterEvaluationOutcome,
    ) -> list[dict[str, Any]]:
        if not outcome.artifacts:
            raise TrainingBackendError("artifact_missing", "worker backend produced no result artifacts")
        return [self._artifact_metadata(job, path) for path in outcome.artifacts]

    def _artifact_metadata(self, job: Any, path: Path) -> dict[str, Any]:
        root = self._artifact_root(job).resolve()
        if path.is_symlink():
            raise TrainingBackendError("artifact_boundary_violation", "symbolic-link artifacts are forbidden")
        resolved = path.resolve()
        try:
            name = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise TrainingBackendError("artifact_boundary_violation", "backend artifact escaped its job root") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise TrainingBackendError("artifact_missing", "backend artifact is not a regular file")
        return {
            "name": name,
            "sha256": file_sha256(resolved),
            "size_bytes": resolved.stat().st_size,
            "media_type": media_type(resolved),
        }

    def _write_training_manifest(
        self,
        job: Any,
        dataset: Any,
        outcome: TrainingOutcome,
        artifacts: list[dict[str, Any]],
    ) -> Path:
        assert isinstance(job.request, TrainingJobRequest)
        manifest = self._artifact_root(job) / "training_manifest.json"
        package_versions: dict[str, str] = {}
        for package in ("torch", "transformers", "datasets", "peft", "trl", "safetensors", "unsloth"):
            try:
                package_versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                continue
        payload = {
            "schema_version": "ananta.lora-training-manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "job_id": job.request.job_id,
            "attempt_id": job.request.attempt_id,
            "fencing_token": job.request.fencing_token,
            "correlation_id": job.request.correlation_id,
            "backend": job.request.backend,
            "base_model": asdict(job.request.base_model),
            "dataset": {
                **asdict(job.request.dataset),
                "identity_hash": job.request.dataset.identity_hash,
                "verified_train_records": dataset.train_records,
                "verified_validation_records": dataset.validation_records,
            },
            "configuration": {
                **asdict(job.request.configuration),
                "identity_hash": job.request.configuration.identity_hash,
            },
            "governance": asdict(job.request.governance) if job.request.governance is not None else None,
            "metrics": dict(outcome.metrics),
            "artifacts": artifacts,
            "software": {"python": platform.python_version(), "packages": package_versions},
            "hardware": {
                "resource_profile": self._config.resource_profile,
                "machine": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
                "cuda_visible_devices": str(os.getenv("CUDA_VISIBLE_DEVICES", "")),
            },
            "completed_at": time.time(),
        }
        manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return manifest

    def _write_evaluation_manifest(
        self,
        job: Any,
        dataset: Any,
        outcome: AdapterEvaluationOutcome,
        artifacts: list[dict[str, Any]],
    ) -> Path:
        request = job.request
        assert isinstance(request, AdapterEvaluationJobRequest)
        manifest = self._artifact_root(job) / "evaluation_manifest.json"
        payload = {
            "schema_version": "ananta.adapter-evaluation-manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "fencing_token": request.fencing_token,
            "correlation_id": request.correlation_id,
            "backend": request.backend,
            "base_model": asdict(request.base_model),
            "adapter": asdict(request.adapter),
            "validation_dataset": {
                **asdict(request.validation_dataset),
                "identity_hash": request.validation_dataset.identity_hash,
                "verified_validation_records": dataset.validation_records,
            },
            "configuration": {
                **asdict(request.configuration),
                "identity_hash": request.configuration.identity_hash,
            },
            "metrics": dict(outcome.metrics),
            "artifacts": artifacts,
            "software": {"python": platform.python_version(), "packages": self._package_versions()},
            "hardware": self._hardware_manifest(),
            "completed_at": time.time(),
        }
        manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return manifest

    def _checkpoint_metadata(self, job: Any, checkpoint: Path) -> dict[str, Any]:
        assert isinstance(job.request, TrainingJobRequest)
        root = self._checkpoint_root(job).resolve()
        resolved = checkpoint.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise TrainingBackendError("checkpoint_boundary_violation", "best checkpoint escaped its job root") from exc
        digest = path_sha256(resolved)
        relative = resolved.relative_to(self._config.state_root.resolve()).as_posix()
        return {
            "relative_path": relative,
            "binding": {
                "job_id": job.request.job_id,
                "source_attempt_id": job.request.attempt_id,
                "base_model_hash": job.request.base_model.snapshot_hash,
                "dataset_hash": job.request.dataset.identity_hash,
                "configuration_hash": job.request.configuration.identity_hash,
                "checkpoint_sha256": digest,
            },
        }

    def _resume_path(self, job: Any) -> Path | None:
        assert isinstance(job.request, TrainingJobRequest)
        resume = job.request.resume_checkpoint
        if resume is None:
            return None
        path_parts = Path(resume.relative_path).parts
        legacy_prefix = (
            "jobs",
            resume.binding.job_id,
            "attempts",
            resume.binding.source_attempt_id,
        )
        scoped_prefix = (
            "tenants",
            job.request.tenant_scope_digest,
            "jobs",
            resume.binding.job_id,
            "attempts",
            resume.binding.source_attempt_id,
        )
        if path_parts[: len(scoped_prefix)] == scoped_prefix:
            source_prefix = scoped_prefix
        elif path_parts[: len(legacy_prefix)] == legacy_prefix:
            source_prefix = legacy_prefix
        else:
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint path is not bound to its source job",
            )
        source_status_path = self._config.state_root.joinpath(
            *source_prefix,
            "status.json",
        )
        try:
            source_status = json.loads(source_status_path.read_text(encoding="utf-8"))
            source_request = TrainingJobRequest.from_mapping(source_status["request"])
        except (FileNotFoundError, KeyError, json.JSONDecodeError, TrainingContractError) as exc:
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint source job cannot be verified",
            ) from exc
        if (
            source_request.job_id != resume.binding.job_id
            or source_request.attempt_id != resume.binding.source_attempt_id
            or source_request.tenant_scope_digest != job.request.tenant_scope_digest
            or source_request.backend != job.request.backend
            or source_request.base_model.snapshot_hash != resume.binding.base_model_hash
            or source_request.dataset.identity_hash != resume.binding.dataset_hash
            or source_request.configuration.identity_hash != resume.binding.configuration_hash
        ):
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint source provenance does not match its binding",
            )
        path = self._resolve_within(self._config.state_root, resume.relative_path, "checkpoint_missing")
        if not path.exists():
            raise TrainingContractError("checkpoint_missing", "resume checkpoint does not exist")
        if path_sha256(path) != resume.binding.checkpoint_sha256:
            raise TrainingContractError(
                "checkpoint_hash_mismatch", "resume checkpoint SHA-256 does not match its binding"
            )
        return path

    @staticmethod
    def _resolve_within(root: Path, relative: str, missing_code: str) -> Path:
        base = root.resolve()
        unresolved = base / relative
        current = base
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise TrainingContractError(missing_code, "symbolic-link resources are not admitted")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise TrainingContractError("invalid_path", "resource path escapes its configured root") from exc
        return candidate

    def _job_root(self, job: Any) -> Path:
        return (
            self._config.state_root
            / "tenants"
            / job.request.tenant_scope_digest
            / "jobs"
            / job.request.job_id
            / "attempts"
            / job.request.attempt_id
        )

    def _artifact_root(self, job: Any) -> Path:
        return self._job_root(job) / "artifacts"

    def _checkpoint_root(self, job: Any) -> Path:
        return self._job_root(job) / "checkpoints"

    def _event_path(self, job: Any) -> Path:
        return self._job_root(job) / "events.jsonl"

    def _status_path(self, job: Any) -> Path:
        return self._job_root(job) / "status.json"

    def _workspace_relative(self, job: Any) -> str:
        return (
            f"tenants/{job.request.tenant_scope_digest}/jobs/"
            f"{job.request.job_id}/attempts/{job.request.attempt_id}/workspace"
        )

    def _write_model_binding(self, job: Any) -> None:
        binding = self._job_root(job) / "model-binding.json"
        binding.write_text(
            json.dumps(
                {
                    "schema": "ananta.lora-model-binding.v1",
                    "tenant_scope_digest": job.request.tenant_scope_digest,
                    "job_id": job.request.job_id,
                    "attempt_id": job.request.attempt_id,
                    "model_id": job.request.base_model.model_id,
                    "snapshot_hash": job.request.base_model.snapshot_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _enforce_dataset_scope_and_quota(self, job: Any) -> None:
        request = job.request
        if isinstance(request, TrainingJobRequest):
            manifests = (request.dataset.train, request.dataset.validation)
        else:
            manifests = (request.validation_dataset.validation,)
        total = 0
        for manifest in manifests:
            relative = str(manifest.relative_path)
            parts = Path(relative).parts
            if parts[:1] == ("tenants",) and (
                len(parts) < 3 or parts[1] != str(request.tenant_storage_key) or parts[2] != "datasets"
            ):
                raise TrainingContractError(
                    "dataset_tenant_binding_mismatch",
                    "dataset path belongs to another tenant",
                )
            path = self._resolve_within(
                self._config.dataset_root,
                relative,
                "dataset_missing",
            )
            if path.is_symlink() or not path.is_file():
                raise TrainingContractError(
                    "dataset_missing",
                    "dataset split is not a regular file",
                )
            total += path.stat().st_size
        if total > self._config.max_dataset_bytes:
            raise TrainingContractError(
                "dataset_quota_exceeded",
                "combined dataset splits exceed the configured quota",
            )

    def _enforce_storage_quotas(self, job: Any) -> None:
        self._enforce_path_quota(
            self._checkpoint_root(job),
            self._config.max_checkpoint_bytes,
            "checkpoint_quota_exceeded",
        )
        self._enforce_path_quota(
            self._artifact_root(job),
            self._config.max_export_bytes,
            "export_quota_exceeded",
        )
        tenant_state = self._config.state_root / "tenants" / job.request.tenant_scope_digest
        tenant_workspace = self._config.workspace_root / "tenants" / job.request.tenant_scope_digest
        if path_size(tenant_state) + path_size(tenant_workspace) > self._config.max_tenant_bytes:
            raise TrainingContractError(
                "tenant_storage_quota_exceeded",
                "worker tenant storage exceeds the configured quota",
            )

    @staticmethod
    def _enforce_path_quota(
        path: Path,
        maximum: int,
        reason_code: str,
    ) -> None:
        if path_size(path) > maximum:
            raise TrainingContractError(
                reason_code,
                "worker storage exceeds the configured quota",
            )

    def _storage_usage(self, job: Any) -> dict[str, Any]:
        checkpoint_bytes = path_size(self._checkpoint_root(job))
        export_bytes = path_size(self._artifact_root(job))
        workspace = self._resolve_within(
            self._config.workspace_root,
            job.request.workspace_ref,
            "workspace_missing",
        )
        return {
            "workspace_bytes": path_size(workspace),
            "checkpoint_bytes": checkpoint_bytes,
            "export_bytes": export_bytes,
            "attempt_bytes": checkpoint_bytes + export_bytes,
            "quotas": {
                "dataset_bytes": self._config.max_dataset_bytes,
                "model_bytes": self._config.max_model_bytes,
                "checkpoint_bytes": self._config.max_checkpoint_bytes,
                "export_bytes": self._config.max_export_bytes,
                "tenant_total_bytes": self._config.max_tenant_bytes,
            },
        }

    @staticmethod
    def _package_versions() -> dict[str, str]:
        versions: dict[str, str] = {}
        for package in (
            "torch",
            "transformers",
            "datasets",
            "peft",
            "trl",
            "safetensors",
            "unsloth",
            "unsloth_zoo",
        ):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                continue
        return versions

    @staticmethod
    def _bounded_torch_hardware_probe() -> dict[str, Any]:
        script = (
            "import json, torch\n"
            "available=bool(torch.cuda.is_available())\n"
            "count=int(torch.cuda.device_count()) if available else 0\n"
            "props=torch.cuda.get_device_properties(0) if count else None\n"
            "print(json.dumps({'cuda_available':available,'torch_version':str(torch.__version__),"
            "'cuda_version':str(torch.version.cuda) if torch.version.cuda else None,"
            "'device_count':count,'device_name':str(props.name) if props else None,"
            "'total_vram_bytes':int(props.total_memory) if props else None}))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if completed.returncode != 0 or len(completed.stdout) > 16_384:
                return {}
            payload = json.loads(completed.stdout)
            return dict(payload) if isinstance(payload, Mapping) else {}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {}

    def _hardware_manifest(self) -> dict[str, Any]:
        return {
            "resource_profile": self._config.resource_profile,
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "cuda_visible_devices": str(os.getenv("CUDA_VISIBLE_DEVICES", "")),
        }


def safe_resource_admission_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != RESOURCE_ADMISSION_PAYLOAD_KEYS:
        raise TrainingBackendError(
            "invalid_backend_event",
            "resource admission event must contain exactly the declared fields",
        )
    profile = payload.get("profile")
    if not isinstance(profile, str) or not profile or len(profile) > 64:
        raise TrainingBackendError("invalid_backend_event", "resource admission profile is invalid")
    admitted = payload.get("admitted")
    estimate_only = payload.get("estimate_only")
    if not isinstance(admitted, bool) or not isinstance(estimate_only, bool):
        raise TrainingBackendError("invalid_backend_event", "resource admission flags are invalid")
    if payload.get("reason_code") != "vram_admission_admitted":
        raise TrainingBackendError("invalid_backend_event", "resource admission reason is invalid")
    clean: dict[str, Any] = {
        "profile": profile,
        "admitted": admitted,
        "estimate_only": estimate_only,
        "reason_code": "vram_admission_admitted",
    }
    for field_name in ("estimated_peak_bytes", "reserve_bytes"):
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
            raise TrainingBackendError(
                "invalid_backend_event",
                f"resource admission {field_name} is invalid",
            )
        clean[field_name] = value
    usable_bytes = payload.get("usable_bytes")
    if usable_bytes is not None and (
        isinstance(usable_bytes, bool) or not isinstance(usable_bytes, int) or not 0 <= usable_bytes <= 2**63 - 1
    ):
        raise TrainingBackendError("invalid_backend_event", "resource admission usable_bytes is invalid")
    clean["usable_bytes"] = usable_bytes
    assumptions = payload.get("assumptions")
    if (
        not isinstance(assumptions, (list, tuple))
        or not 1 <= len(assumptions) <= 16
        or any(not isinstance(item, str) or not item or len(item) > 256 for item in assumptions)
    ):
        raise TrainingBackendError("invalid_backend_event", "resource admission assumptions are invalid")
    clean["assumptions"] = list(assumptions)
    return clean


def safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise TrainingBackendError("invalid_backend_event", "backend event contains a non-finite number")
        return value
    raise TrainingBackendError("invalid_backend_event", "backend event values must be scalar")


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_sha256(path: Path) -> str:
    if path.is_symlink():
        raise TrainingContractError("invalid_path", "hashed resource must not be a symbolic link")
    if path.is_file():
        return file_sha256(path)
    if not path.is_dir():
        raise TrainingContractError("invalid_path", "hashed resource is not a regular file or directory")
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise TrainingContractError("invalid_path", "hashed resource tree contains a symbolic link")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise TrainingContractError("invalid_path", "hashed resource tree contains an unsupported entry")
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise TrainingContractError("invalid_path", "hashed resource tree is empty")
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".safetensors": "application/octet-stream",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")
