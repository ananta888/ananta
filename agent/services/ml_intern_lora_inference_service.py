"""Hub control service for approved Base+LoRA worker inference.

No ML framework is imported here.  The Hub validates policy and artifacts,
materializes an immutable hash-bound adapter view, and delegates execution to
the isolated LoRA worker port.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from flask import current_app, has_app_context

from agent.services.ml_intern_adapter_registry_service import (
    AdapterRecord,
    MlInternAdapterRegistryService,
)
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityError,
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_lora_inference_contract import (
    GENERATION_CAPABILITY,
    LoraInferenceContractError,
    LoraInferenceRequest,
    LoraInferenceResult,
    MaterializedAdapter,
    approval_decision,
    build_worker_envelope,
    canonical_sha256,
)
from agent.services.ml_intern_lora_inference_worker_port import (
    LoraInferenceWorkerPort,
    LoraInferenceWorkerTransportError,
    lora_inference_worker_port_from_environment,
)


class LoraInferenceError(RuntimeError):
    """Policy, artifact, configuration, or worker failure."""

    def __init__(self, reason_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


_MATERIALIZE_LOCK = threading.RLock()


class MlInternLoraInferenceService:
    """Validate an approved adapter and delegate generation through a port."""

    def __init__(
        self,
        *,
        registry: MlInternAdapterRegistryService,
        artifact_root: str | Path,
        workspace_root: str | Path,
        model_catalog: Mapping[str, Mapping[str, Any]],
        worker_port: LoraInferenceWorkerPort | None,
        max_adapter_bytes: int = 2 * 1024**3,
        timeout_seconds: int = 120,
    ) -> None:
        if not 1_024 <= int(max_adapter_bytes) <= 64 * 1024**3:
            raise ValueError("max_adapter_bytes is outside its bounds")
        if not 1 <= int(timeout_seconds) <= 300:
            raise ValueError("LoRA inference timeout is outside its bounds")
        self._registry = registry
        self._artifact_store = MlInternArtifactSecurityService(
            storage_root=artifact_root,
            policy=ArtifactSecurityPolicy(
                max_file_bytes=int(max_adapter_bytes),
                max_request_bytes=int(max_adapter_bytes),
                max_tenant_bytes=max(int(max_adapter_bytes), int(max_adapter_bytes) * 8),
                max_archive_uncompressed_bytes=int(max_adapter_bytes),
            ),
        )
        self._workspace_store = MlInternArtifactSecurityService(
            storage_root=workspace_root,
            policy=ArtifactSecurityPolicy(
                max_file_bytes=int(max_adapter_bytes),
                max_request_bytes=int(max_adapter_bytes),
                max_tenant_bytes=max(int(max_adapter_bytes), int(max_adapter_bytes) * 8),
                max_archive_uncompressed_bytes=int(max_adapter_bytes),
            ),
        )
        self._models = _normalize_model_catalog(model_catalog)
        self._worker_port = worker_port
        self._timeout_seconds = int(timeout_seconds)

    def generate(
        self,
        request: LoraInferenceRequest,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> LoraInferenceResult:
        if not isinstance(request, LoraInferenceRequest):
            raise TypeError("request must be a LoraInferenceRequest")
        record = self._require_approved_record(
            request,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        if self._worker_port is None:
            raise LoraInferenceError(
                "lora_inference_worker_unconfigured",
                "No isolated LoRA inference worker is configured",
                retryable=True,
            )
        model = self._models.get(request.base_model)
        if model is None:
            raise LoraInferenceError(
                "base_model_not_in_local_catalog",
                "Approved adapter base model is not in the local worker catalog",
            )
        materialized = self._materialize(record)
        approval = approval_decision(
            adapter_id=record.adapter_id,
            adapter_version=record.version,
            adapter_sha256=materialized.directory_sha256,
            base_model=record.base_model,
            task_kind=request.task_kind,
            approved_at=str(record.approved_at or ""),
        )
        envelope = build_worker_envelope(
            request=request,
            adapter=materialized,
            base_model={"model_id": request.base_model, **model},
            approval=approval,
            deadline_epoch_ms=int((time.time() + self._timeout_seconds) * 1000),
        )
        try:
            response = self._worker_port.generate(envelope)
        except (LoraInferenceWorkerTransportError, LoraInferenceContractError) as exc:
            raise LoraInferenceError(
                str(getattr(exc, "reason_code", "lora_inference_worker_failed")),
                "Approved adapter worker inference failed",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        return LoraInferenceResult(
            text=str(response["output"]),
            worker_id=self._worker_port.worker_id,
            capability=GENERATION_CAPABILITY,
            adapter_id=record.adapter_id,
            adapter_version=record.version,
            reason_code=str(response.get("reason_code") or "approved_adapter_inference_succeeded"),
        )

    def unload(
        self,
        *,
        adapter_id: str,
        reason: str,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> Mapping[str, Any]:
        record = self._registry.get(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        if record is None:
            raise LoraInferenceError("adapter_not_found", "Adapter is not registered")
        if self._worker_port is None:
            raise LoraInferenceError(
                "lora_inference_worker_unconfigured",
                "No isolated LoRA inference worker is configured",
                retryable=True,
            )
        try:
            return self._worker_port.unload(
                adapter_id=record.adapter_id,
                adapter_version=record.version,
                reason=reason,
            )
        except (LoraInferenceWorkerTransportError, LoraInferenceContractError) as exc:
            raise LoraInferenceError(
                str(getattr(exc, "reason_code", "lora_inference_unload_failed")),
                "LoRA inference cache unload failed",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc

    def capabilities(self) -> Mapping[str, Any]:
        if self._worker_port is None:
            return {
                "available": False,
                "reason_code": "lora_inference_worker_unconfigured",
                "capabilities": [],
            }
        try:
            return self._worker_port.capabilities()
        except LoraInferenceWorkerTransportError as exc:
            return {
                "available": False,
                "reason_code": exc.reason_code,
                "capabilities": [],
            }

    def _require_approved_record(
        self,
        request: LoraInferenceRequest,
        *,
        tenant_id: str | None,
        owner_subject: str | None,
    ) -> AdapterRecord:
        record = self._registry.get(
            request.adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        if record is None:
            raise LoraInferenceError("adapter_not_found", "Selected adapter is not registered")
        if record.status != "approved" or not record.approved_at:
            raise LoraInferenceError(
                "adapter_not_approved",
                "Only an explicitly approved adapter may be used for inference",
            )
        if record.version != request.adapter_version:
            raise LoraInferenceError("adapter_version_mismatch", "Selected adapter version changed")
        if record.base_model != request.base_model:
            raise LoraInferenceError("adapter_base_model_mismatch", "Adapter does not match the base model")
        if record.task_kinds and request.task_kind not in record.task_kinds:
            raise LoraInferenceError("adapter_task_kind_mismatch", "Adapter does not support this task kind")
        return record

    def _materialize(self, record: AdapterRecord) -> MaterializedAdapter:
        raw_path = (
            record.artifact_paths.get("adapter_dir")
            or record.artifact_paths.get("adapter_path")
            or record.artifact_paths.get("output_dir")
        )
        if not raw_path:
            raise LoraInferenceError("adapter_artifact_missing", "Approved adapter has no artifact")
        try:
            source = self._artifact_store.ensure_internal_path(raw_path, must_exist=True)
            inspection = self._artifact_store.validate_adapter_tree(source)
            if int(inspection.get("total_bytes") or 0) > self._artifact_store.policy.max_request_bytes:
                raise ArtifactSecurityError(
                    "adapter_too_large",
                    "approved adapter exceeds the inference size limit",
                )
        except ArtifactSecurityError as exc:
            raise LoraInferenceError(exc.reason_code, "Approved adapter artifact failed verification") from exc
        files = tuple(
            {
                "name": str(row["name"]),
                "sha256": str(row["sha256"]),
                "size_bytes": int(row["size_bytes"]),
            }
            for row in sorted(inspection["files"], key=lambda item: str(item["name"]))
        )
        directory_digest = canonical_sha256(list(files))
        approved_digest = str(record.artifact_sha256 or "").strip().lower()
        if not approved_digest:
            raise LoraInferenceError(
                "adapter_artifact_not_hash_bound",
                "Approved adapter has no immutable artifact hash",
            )
        if approved_digest != directory_digest:
            raise LoraInferenceError(
                "adapter_artifact_approval_hash_mismatch",
                "Approved adapter artifact no longer matches its approval binding",
            )
        identity_key = hashlib.sha256(f"{record.adapter_id}\0{record.version}".encode()).hexdigest()[:24]
        destination_relative = f"lora-inference/adapters/{identity_key}/{directory_digest}"
        try:
            with _MATERIALIZE_LOCK:
                destination = self._workspace_store.resolve_relative(destination_relative)
                if destination.exists():
                    _verify_materialized_tree(destination, files)
                else:
                    _copy_verified_tree(source, destination, files)
            self._workspace_store.ensure_internal_path(destination, must_exist=True)
        except (ArtifactSecurityError, OSError) as exc:
            reason_code = str(getattr(exc, "reason_code", "adapter_materialization_failed"))
            raise LoraInferenceError(reason_code, "Approved adapter could not be materialized") from exc
        return MaterializedAdapter(
            adapter_id=record.adapter_id,
            version=record.version,
            relative_path=destination_relative,
            directory_sha256=directory_digest,
            files=files,
        )


def resolve_lora_storage_config(agent_config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = dict(agent_config or {})
    training = dict(config.get("ml_intern_training") or {})
    runtime = dict(config.get("lora_runtime") or {})
    artifact_root = str(
        os.getenv("ANANTA_LORA_TRAINING_ARTIFACT_ROOT") or training.get("artifact_root") or "artifacts/lora"
    ).strip()
    workspace_root = str(
        os.getenv("ANANTA_LORA_TRAINING_WORKSPACE_ROOT")
        or training.get("workspace_root")
        or "project-workspaces/lora-training"
    ).strip()
    explicit_registry = str(runtime.get("adapter_registry_path") or "").strip()
    registry_path = explicit_registry or str(Path(artifact_root) / "adapter_registry.json")
    return {
        "artifact_root": artifact_root,
        "workspace_root": workspace_root,
        "registry_path": registry_path,
        "max_adapter_bytes": int(training.get("max_adapter_bytes") or 2 * 1024**3),
        "timeout_seconds": int(
            os.getenv("ANANTA_LORA_INFERENCE_TIMEOUT_SECONDS") or runtime.get("timeout_seconds") or 120
        ),
    }


def _model_catalog(agent_config: Mapping[str, Any] | None) -> Mapping[str, Mapping[str, Any]]:
    raw_environment = str(os.getenv("ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON") or "").strip()
    if raw_environment:
        try:
            value = json.loads(raw_environment)
        except ValueError as exc:
            raise RuntimeError("LoRA inference model catalog JSON is invalid") from exc
    else:
        training = dict((agent_config or {}).get("ml_intern_training") or {})
        value = training.get("base_model_catalog") or {}
    if not isinstance(value, Mapping):
        raise RuntimeError("LoRA inference model catalog must be an object")
    return value  # type: ignore[return-value]


def _normalize_model_catalog(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for raw_id, raw in value.items():
        model_id = str(raw_id or "").strip()
        if not model_id or len(model_id) > 512 or not isinstance(raw, Mapping):
            raise ValueError("LoRA inference model catalog contains an invalid model")
        relative = str(raw.get("relative_path") or "").strip()
        pure = PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("LoRA inference model catalog contains an invalid relative path")
        digest = str(raw.get("snapshot_hash") or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("LoRA inference model catalog contains an invalid snapshot hash")
        result[model_id] = {"relative_path": relative, "snapshot_hash": digest}
    return result


def _copy_verified_tree(source: Path, destination: Path, files: tuple[dict[str, Any], ...]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=".lora-inference-", dir=str(destination.parent)))
    os.chmod(stage, 0o700)
    try:
        for row in files:
            name = str(row["name"])
            source_file = source / name
            source_descriptor = os.open(
                source_file,
                os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0)) | int(getattr(os, "O_NONBLOCK", 0)),
            )
            target = stage / name
            target_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            digest = hashlib.sha256()
            total = 0
            try:
                source_stat = os.fstat(source_descriptor)
                if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size != row["size_bytes"]:
                    raise ArtifactSecurityError(
                        "promotion_manifest_mismatch",
                        "adapter source is no longer the approved regular file",
                    )
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    digest.update(chunk)
                    _write_all(target_descriptor, chunk)
                os.fsync(target_descriptor)
            finally:
                os.close(source_descriptor)
                os.close(target_descriptor)
            if total != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                raise ArtifactSecurityError("hash_mismatch", "adapter changed during materialization")
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while materializing approved adapter")
        remaining = remaining[written:]


def _verify_materialized_tree(destination: Path, files: tuple[dict[str, Any], ...]) -> None:
    if not destination.is_dir() or destination.is_symlink():
        raise ArtifactSecurityError("invalid_adapter_tree", "materialized adapter is invalid")
    expected = {str(row["name"]): row for row in files}
    actual = list(destination.iterdir())
    unsafe_entry = any(path.is_symlink() or not path.is_file() for path in actual)
    if unsafe_entry or {path.name for path in actual} != set(expected):
        raise ArtifactSecurityError("promotion_manifest_mismatch", "materialized adapter file set differs")
    for path in actual:
        row = expected[path.name]
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if path.stat().st_size != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
            raise ArtifactSecurityError("hash_mismatch", "materialized adapter hash differs")


_service_lock = threading.Lock()
_service_instance: MlInternLoraInferenceService | None = None
_service_signature: str | None = None


def get_lora_inference_service() -> MlInternLoraInferenceService:
    global _service_instance, _service_signature
    agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
    storage = resolve_lora_storage_config(agent_config)
    catalog = _model_catalog(agent_config)
    signature = canonical_sha256(
        {
            "storage": storage,
            "catalog": catalog,
            "worker_url": os.getenv("ANANTA_LORA_INFERENCE_WORKER_URL") or os.getenv("ANANTA_LORA_TRAINING_WORKER_URL"),
            "allowlist": os.getenv("ANANTA_LORA_INFERENCE_ALLOWED_ENDPOINTS")
            or os.getenv("ANANTA_LORA_TRAINING_ALLOWED_ENDPOINTS"),
        }
    )
    with _service_lock:
        if _service_instance is None or _service_signature != signature:
            _service_instance = MlInternLoraInferenceService(
                registry=MlInternAdapterRegistryService(storage["registry_path"]),
                artifact_root=storage["artifact_root"],
                workspace_root=storage["workspace_root"],
                model_catalog=catalog,
                worker_port=lora_inference_worker_port_from_environment(),
                max_adapter_bytes=storage["max_adapter_bytes"],
                timeout_seconds=storage["timeout_seconds"],
            )
            _service_signature = signature
        return _service_instance


__all__ = [
    "LoraInferenceError",
    "LoraInferenceRequest",
    "LoraInferenceResult",
    "MlInternLoraInferenceService",
    "get_lora_inference_service",
    "resolve_lora_storage_config",
]
