"""Secure Hub transport for the isolated LoRA-training worker.

The Hub owns admission and polling.  This adapter only translates an admitted
Hub job into the worker's versioned execution contract; it never imports an ML
runtime and it refuses redirects, public addresses and non-allowlisted URLs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    progress_telemetry,
    validate_progress_telemetry,
    validate_worker_capability_probe,
)
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)

WORKER_CONTRACT_VERSION = "ananta.lora-training.v1"
_WORKER_BASE_PATH = "/internal/v1/lora-training"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRAINING_BACKENDS = frozenset(
    {
        "mock",
        "peft_trl",
        "unsloth",
        "unsloth_vision",
        "unsloth_audio",
        "unsloth_embedding",
    }
)
_EVENT_MODALITIES = frozenset({"text", "vision", "audio", "embedding"})
_RESOURCE_ADMISSION_PAYLOAD_FIELDS = frozenset(
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
_WORKER_STATUS_FIELDS = frozenset(
    {
        "contract_version",
        "job_id",
        "attempt_id",
        "fencing_token",
        "correlation_id",
        "job_type",
        "backend",
        "status",
        "created_at",
        "updated_at",
        "heartbeat_at",
        "progress",
        "metrics",
        "artifacts",
    "resume_checkpoint",
    "storage_usage",
        "cancel_mode",
        "error",
    }
)
_WORKER_EVENT_FIELDS = frozenset(
    {
        "contract_version",
        "sequence",
        "timestamp",
        "job_id",
        "attempt_id",
        "fencing_token",
        "correlation_id",
        "type",
        "payload",
    }
)
_WORKER_EVENT_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "accepted": frozenset({"backend"}),
    "status": frozenset({"status", "reason_code", "retryable"}),
    "phase": frozenset({"phase", "step", "modality"}),
    "progress": frozenset(
        {
            "step",
            "max_steps",
            "epoch",
            "loss",
            "eval_loss",
            "learning_rate",
            "tokens_per_second",
            "gpu_utilization_percent",
            "vram_used_bytes",
            "telemetry",
        }
    ),
    "checkpoint": frozenset({"step", "name", "sha256"}),
    "artifact": frozenset({"name", "sha256", "size_bytes", "media_type"}),
    "resource_admission": _RESOURCE_ADMISSION_PAYLOAD_FIELDS,
}


class MlInternTrainingWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise MlInternTrainingWorkerTransportError(
            "worker_redirect_forbidden",
            "LoRA training worker transport refused a redirect",
            retryable=False,
        )


class HttpMlInternTrainingWorkerPort:
    """Authenticated, bounded polling adapter implementing the execution port."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        dataset_root: str | Path,
        workspace_root: str | Path,
        model_root: str | Path | None = None,
        artifact_root: str | Path,
        model_catalog: Mapping[str, Mapping[str, Any]],
        adapter_resolver: Callable[[str, str], str | Path] | None = None,
        admitted_backends: Sequence[str] = (
            "mock",
            "peft_trl",
            "unsloth",
            "unsloth_vision",
            "unsloth_audio",
            "unsloth_embedding",
        ),
        resource_profile: str = "nvidia",
        timeout_seconds: int = 3600,
        connect_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.5,
        max_response_bytes: int = 4 * 1024 * 1024,
        max_artifact_bytes: int = 2 * 1024 * 1024 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized = normalize_training_worker_endpoint(endpoint)
        allowed = {normalize_training_worker_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowed:
            raise ValueError("LoRA training worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("LoRA training worker bearer token must contain at least 24 non-whitespace characters")
        if not 60 <= int(timeout_seconds) <= 86_400:
            raise ValueError("LoRA training timeout is outside its bounds")
        if not 0 < float(connect_timeout_seconds) <= 60:
            raise ValueError("LoRA training connect timeout is outside its bounds")
        if not 0.05 <= float(poll_interval_seconds) <= 30:
            raise ValueError("LoRA training polling interval is outside its bounds")
        if not 1024 <= int(max_response_bytes) <= 64 * 1024 * 1024:
            raise ValueError("LoRA training response limit is outside its bounds")

        self._parsed = urllib.parse.urlsplit(normalized)
        self._token = token
        self._dataset_root = _existing_root(dataset_root, "dataset_root")
        self._workspace_root = _writable_root(workspace_root, "workspace_root")
        # Kept as an optional compatibility argument. Model files are resolved
        # exclusively by the worker from its read-only model mount; the Hub is
        # intentionally not granted access to model weights.
        del model_root
        self._artifact_store = MlInternArtifactSecurityService(
            storage_root=artifact_root,
            policy=ArtifactSecurityPolicy(
                max_file_bytes=max_artifact_bytes,
                max_request_bytes=max_artifact_bytes,
                max_tenant_bytes=max(max_artifact_bytes, max_artifact_bytes * 8),
                max_archive_uncompressed_bytes=max_artifact_bytes,
            ),
        )
        self._workspace_store = MlInternArtifactSecurityService(
            storage_root=self._workspace_root,
            policy=ArtifactSecurityPolicy(
                max_file_bytes=max_artifact_bytes,
                max_request_bytes=max_artifact_bytes,
                max_tenant_bytes=max(max_artifact_bytes, max_artifact_bytes * 8),
                max_archive_uncompressed_bytes=max_artifact_bytes,
            ),
        )
        self._models = _normalize_model_catalog(model_catalog)
        self._adapter_resolver = adapter_resolver
        self._admitted_backends = frozenset(str(value).strip().lower() for value in admitted_backends)
        if not self._admitted_backends or not self._admitted_backends.issubset(_TRAINING_BACKENDS):
            raise ValueError("LoRA training worker backend capabilities are invalid")
        self._resource_profile = str(resource_profile or "").strip().lower()
        if self._resource_profile not in {"mock", "cpu", "nvidia"}:
            raise ValueError("LoRA training worker resource profile is invalid")
        self._timeout_seconds = int(timeout_seconds)
        self._connect_timeout = float(connect_timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._resolver = resolver
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        self._clock = clock
        self._sleeper = sleeper
        self._capability_cache: tuple[float, dict[str, Any]] | None = None

    @property
    def worker_id(self) -> str:
        return "lora-training-worker"

    @property
    def worker_ref(self) -> str:
        return "internal:lora-training-worker"

    def supports(self, *, job_type: str, backend: str, gpu_profile: str | None = None) -> bool:
        if backend not in self._admitted_backends:
            return False
        try:
            probe = self.capability_probe()
        except MlInternTrainingWorkerTransportError:
            return False
        backend_state = probe["backends"].get(backend)
        if (
            not isinstance(backend_state, Mapping)
            or backend_state.get("available") is not True
            or job_type not in backend_state.get("operations", ())
        ):
            return False
        if gpu_profile is None:
            return True
        profile = probe["gpu_profiles"].get(gpu_profile)
        return isinstance(profile, Mapping) and profile.get("available") is True

    def capability_probe(self) -> Mapping[str, Any]:
        now = self._clock()
        if self._capability_cache is not None:
            cached_at, cached = self._capability_cache
            if 0 <= now - cached_at < 5.0:
                return json.loads(json.dumps(cached))
        payload = self._request_json("GET", "/capabilities", None)
        try:
            probe = validate_worker_capability_probe(payload)
        except UnslothWorkerCapabilityContractError as exc:
            raise MlInternTrainingWorkerTransportError(
                "worker_capability_contract_invalid",
                "LoRA training Worker capability response is incompatible",
                retryable=True,
            ) from exc
        self._capability_cache = (now, probe)
        return json.loads(json.dumps(probe))

    def execute(
        self,
        *,
        job_id: str,
        spec: Mapping[str, Any],
        dataset_path: Path,
        validation_path: Path | None,
        attempt_id: str,
        fencing_token: int,
        on_event: Callable[[Mapping[str, Any]], None],
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]:
        job_id = _identifier(job_id, "job_id")
        if validation_path is None:
            raise MlInternTrainingWorkerTransportError(
                "validation_split_required",
                "live LoRA training requires an admitted validation split",
                retryable=False,
            )
        backend = str(spec.get("backend") or "mock").strip().lower()
        requested_job_type = str(spec.get("job_type") or "train_lora")
        gpu_profile = str(spec.get("gpu_profile") or "").strip().lower() or None
        # Preserve concrete transport and contract failures instead of reducing
        # them to a generic capability miss. The cached follow-up keeps the
        # existing supports() projection as the single capability decision.
        self.capability_probe()
        if not self.supports(
            job_type=requested_job_type,
            backend=backend,
            gpu_profile=gpu_profile,
        ):
            raise MlInternTrainingWorkerTransportError(
                "worker_capability_unavailable",
                "selected LoRA worker does not advertise the requested backend capability",
                retryable=True,
            )
        base_model_id = str(spec.get("base_model") or "").strip()
        model = self._models.get(base_model_id)
        if model is None:
            raise MlInternTrainingWorkerTransportError(
                "base_model_not_admitted",
                "requested base model is not in the local training catalog",
                retryable=False,
            )
        validation = self._split_manifest(validation_path, "validation")
        attempt_id = _identifier(attempt_id, "attempt_id")
        if isinstance(fencing_token, bool) or not 1 <= int(fencing_token) <= 2**255 - 1:
            raise MlInternTrainingWorkerTransportError(
                "invalid_fencing_token", "fencing token is invalid", retryable=False
            )
        tenant_scope_digest = str(spec.get("_tenant_scope_digest") or "").strip().lower()
        if not _SHA256.fullmatch(tenant_scope_digest):
            raise MlInternTrainingWorkerTransportError(
                "tenant_scope_binding_required",
                "worker delegation requires an opaque Hub tenant-scope binding",
                retryable=False,
            )
        tenant_storage_key = str(
            spec.get("_tenant_storage_key") or tenant_scope_digest
        ).strip().lower()
        if not _SHA256.fullmatch(tenant_storage_key):
            raise MlInternTrainingWorkerTransportError(
                "tenant_storage_binding_required",
                "worker delegation requires an opaque tenant storage binding",
                retryable=False,
            )
        common = {
            "contract_version": WORKER_CONTRACT_VERSION,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "fencing_token": fencing_token,
            "correlation_id": f"hub-{uuid.uuid4()}",
            "backend": backend,
            "resource_profile": self._resource_profile,
            "tenant_scope_digest": tenant_scope_digest,
            "tenant_storage_key": tenant_storage_key,
            "deadline_epoch_ms": int((self._clock() + self._timeout_seconds) * 1000),
            "base_model": {
                "model_id": base_model_id,
                "relative_path": model["relative_path"],
                "snapshot_hash": model["snapshot_hash"],
            },
        }
        job_type = requested_job_type
        if job_type == "evaluate_lora":
            envelope, submit_suffix = self._evaluation_envelope(
                job_id=job_id,
                spec=spec,
                validation=validation,
                common=common,
            )
        elif job_type == "train_lora":
            train = self._split_manifest(dataset_path, "train")
            workspace_ref = (
                f"tenants/{tenant_scope_digest}/jobs/{job_id}/"
                f"attempts/{attempt_id}/workspace"
            )
            self._workspace_store.resolve_relative(workspace_ref).mkdir(parents=True, exist_ok=True, mode=0o700)
            envelope = {
                **common,
                "job_type": "train_lora",
                "workspace_ref": workspace_ref,
                "dataset": {
                    "dataset_id": _identifier(str(spec.get("dataset_id") or job_id), "dataset_id"),
                    "dataset_version": _identifier(
                        str(spec.get("dataset_version") or train["sha256"][:32]), "dataset_version"
                    ),
                    "train": train,
                    "validation": validation,
                },
                "configuration": _worker_configuration(spec),
            }
            exports = _worker_exports(spec, backend=backend)
            if exports:
                envelope["exports"] = exports
            resume_checkpoint = spec.get("resume_checkpoint")
            if resume_checkpoint is not None:
                if not isinstance(resume_checkpoint, Mapping):
                    raise MlInternTrainingWorkerTransportError(
                        "resume_checkpoint_invalid",
                        "resume checkpoint must be a bound object",
                        retryable=False,
                    )
                envelope["resume_checkpoint"] = dict(resume_checkpoint)
            submit_suffix = "/jobs"
        else:
            raise MlInternTrainingWorkerTransportError(
                "unsupported_job_type",
                "LoRA worker transport only supports training and adapter evaluation",
                retryable=False,
            )
        accepted = self._request_json("POST", submit_suffix, envelope)
        _validate_worker_status(accepted)
        _validate_worker_correlation(
            accepted,
            job_id=job_id,
            attempt_id=attempt_id,
            fencing_token=int(fencing_token),
            correlation_id=str(envelope["correlation_id"]),
        )

        cursor = 0
        cancel_sent = False
        next_heartbeat = self._clock()
        last_checkpoint_digest: str | None = None
        while True:
            if cancel_check() and not cancel_sent:
                cancellation = self._request_json("POST", f"/jobs/{job_id}/cancel", {})
                _validate_worker_status(cancellation)
                _validate_worker_correlation(
                    cancellation,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    fencing_token=int(fencing_token),
                    correlation_id=str(envelope["correlation_id"]),
                )
                cancel_sent = True
            event_page = self._request_json(
                "GET",
                f"/jobs/{job_id}/events?after_sequence={cursor}&limit=200",
                None,
            )
            _validate_worker_event_page(event_page)
            _validate_worker_correlation(
                event_page,
                job_id=job_id,
                attempt_id=attempt_id,
                fencing_token=None,
                correlation_id=None,
            )
            events = event_page.get("events")
            assert isinstance(events, list)
            for event in events:
                assert isinstance(event, Mapping)
                _validate_worker_correlation(
                    event,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    fencing_token=int(fencing_token),
                    correlation_id=str(envelope["correlation_id"]),
                )
                on_event(_project_worker_event(event))
            cursor = int(event_page["next_sequence"])
            if self._clock() >= next_heartbeat:
                heartbeat = self._request_json("POST", f"/jobs/{job_id}/heartbeat", {})
                _validate_worker_status(heartbeat)
                _validate_worker_correlation(
                    heartbeat,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    fencing_token=int(fencing_token),
                    correlation_id=str(envelope["correlation_id"]),
                )
                on_event({"type": "heartbeat", "phase": "worker_heartbeat"})
                next_heartbeat = self._clock() + max(5.0, min(30.0, self._timeout_seconds / 3))
            status = self._request_json("GET", f"/jobs/{job_id}", None)
            _validate_worker_status(status)
            _validate_worker_correlation(
                status,
                job_id=job_id,
                attempt_id=attempt_id,
                fencing_token=int(fencing_token),
                correlation_id=str(envelope["correlation_id"]),
            )
            resume_checkpoint = status.get("resume_checkpoint")
            if isinstance(resume_checkpoint, Mapping):
                checkpoint_digest = hashlib.sha256(
                    json.dumps(
                        dict(resume_checkpoint),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
                if checkpoint_digest != last_checkpoint_digest:
                    on_event(
                        {
                            "type": "checkpoint",
                            "phase": "checkpoint",
                            "resume_checkpoint": dict(resume_checkpoint),
                        }
                    )
                    last_checkpoint_digest = checkpoint_digest
            state = str(status.get("status") or "")
            if state in {"succeeded", "failed", "cancelled"}:
                return self._terminal_result(job_id, job_type, status, spec)
            if self._clock() * 1000 >= envelope["deadline_epoch_ms"]:
                if not cancel_sent:
                    cancellation = self._request_json("POST", f"/jobs/{job_id}/cancel", {})
                    _validate_worker_status(cancellation)
                    _validate_worker_correlation(
                        cancellation,
                        job_id=job_id,
                        attempt_id=attempt_id,
                        fencing_token=int(fencing_token),
                        correlation_id=str(envelope["correlation_id"]),
                    )
                raise MlInternTrainingWorkerTransportError("timeout", "LoRA training worker deadline expired")
            self._sleeper(self._poll_interval)

    def _evaluation_envelope(
        self,
        *,
        job_id: str,
        spec: Mapping[str, Any],
        validation: Mapping[str, Any],
        common: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        adapter_id = _identifier(str(spec.get("adapter_id") or ""), "adapter_id")
        if self._adapter_resolver is None:
            raise MlInternTrainingWorkerTransportError(
                "adapter_resolver_unavailable",
                "adapter evaluation is not configured with a registry resolver",
                retryable=False,
            )
        source = self._artifact_store.ensure_internal_path(
            self._adapter_resolver(adapter_id, str(common["tenant_scope_digest"])),
            must_exist=True,
        )
        inspected = self._artifact_store.validate_adapter_tree(source)
        workspace_ref = (
            f"tenants/{common['tenant_scope_digest']}/jobs/{job_id}/"
            f"attempts/{common['attempt_id']}/workspace"
        )
        destination_relative = f"{workspace_ref}/adapter"
        destination = self._workspace_store.resolve_relative(destination_relative)
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        expected_files = {
            str(row["name"]): str(row["sha256"]) for row in inspected["files"] if isinstance(row, Mapping)
        }
        total = 0
        try:
            for name, digest in sorted(expected_files.items()):
                source_file = source / name
                read_flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
                descriptor = os.open(source_file, read_flags)
                with os.fdopen(descriptor, "rb") as handle:
                    stored = self._workspace_store.store_upload(
                        handle,
                        destination_relative=f"{destination_relative}/{name}",
                        filename=name,
                        expected_sha256=digest,
                        request_bytes_used=total,
                    )
                total += stored.size_bytes
            self._workspace_store.validate_adapter_tree(destination)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise
        hyperparameters = dict(spec.get("hyperparameters") or {})
        quantization = "4bit" if bool(hyperparameters.get("load_in_4bit", spec.get("method") == "qlora")) else "none"
        envelope = {
            **dict(common),
            "job_type": "evaluate_existing_adapter",
            "workspace_ref": workspace_ref,
            "adapter": {
                "adapter_id": adapter_id,
                "relative_path": "adapter",
                "sha256": _path_sha256(destination),
            },
            "validation_dataset": {
                "dataset_id": _identifier(str(spec.get("dataset_id") or job_id), "dataset_id"),
                "dataset_version": _identifier(
                    str(spec.get("dataset_version") or str(validation["sha256"])[:32]),
                    "dataset_version",
                ),
                "validation": dict(validation),
            },
            "configuration": {
                "seed": int(hyperparameters.get("seed") or 42),
                "batch_size": int(hyperparameters.get("batch_size") or 1),
                "max_sequence_length": int(hyperparameters.get("max_seq_length") or 2048),
                "max_samples": min(int(validation["record_count"]), 100_000),
                "quantization": quantization,
                "scorer_name": str(spec.get("scorer_name") or "generic").strip().lower(),
            },
        }
        return envelope, "/evaluations"

    def _terminal_result(
        self,
        job_id: str,
        job_type: str,
        status: Mapping[str, Any],
        spec: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _validate_worker_status(status)
        state = str(status.get("status") or "failed")
        if state not in {"succeeded", "failed", "cancelled"}:
            raise MlInternTrainingWorkerTransportError(
                "invalid_worker_response",
                "worker terminal result has an unsupported status",
                retryable=False,
            )
        artifacts = status.get("artifacts")
        metrics = status.get("metrics")
        resume_checkpoint = status.get("resume_checkpoint")
        if not isinstance(artifacts, list) or len(artifacts) > 64:
            raise MlInternTrainingWorkerTransportError(
                "invalid_worker_response",
                "worker terminal result has invalid artifact metadata",
                retryable=False,
            )
        if not isinstance(metrics, Mapping):
            raise MlInternTrainingWorkerTransportError(
                "invalid_worker_response",
                "worker terminal result has invalid metrics",
                retryable=False,
            )
        if resume_checkpoint is not None and not isinstance(resume_checkpoint, Mapping):
            raise MlInternTrainingWorkerTransportError(
                "invalid_worker_response",
                "worker terminal result has an invalid resume checkpoint",
                retryable=False,
            )
        if state == "cancelled":
            worker_mode = status.get("cancel_mode")
            return {
                "status": "cancelled",
                "cancelled": True,
                "cancel_mode": "cooperative" if worker_mode == "graceful" else worker_mode,
            }
        if state == "failed":
            error = status.get("error")
            if not isinstance(error, Mapping) or not str(error.get("code") or "").strip() or not str(
                error.get("message") or ""
            ).strip():
                raise MlInternTrainingWorkerTransportError(
                    "invalid_worker_response",
                    "worker failure result is missing its structured error",
                    retryable=False,
                )
            return {
                "status": "failed",
                "error_code": str(error["code"])[:128],
                "error_message": str(error["message"])[:512],
                "retryable": bool(error.get("retryable", False)),
            }
        admitted = self._download_artifacts(
            job_id,
            artifacts,
            job_type=job_type,
            attempt_id=str(status["attempt_id"]),
            tenant_scope_digest=str(spec.get("_tenant_scope_digest") or ""),
        )
        adapter_id = (
            str(spec.get("adapter_id") or "")
            if job_type == "evaluate_lora"
            else f"adapter-{job_id.removeprefix('lora-job-')}"
        )
        return {
            "status": "completed",
            "adapter_id": adapter_id,
            "result_ref": f"lora-artifact:{job_id}",
            "artifacts": admitted,
            "metrics": dict(metrics),
            "resume_checkpoint": dict(resume_checkpoint) if resume_checkpoint is not None else None,
        }

    def _download_artifacts(
        self,
        job_id: str,
        artifacts: Sequence[Any],
        *,
        job_type: str,
        attempt_id: str,
        tenant_scope_digest: str,
    ) -> list[dict[str, Any]]:
        admitted: list[dict[str, Any]] = []
        total = 0
        base = (
            f"tenants/{_identifier(tenant_scope_digest, 'tenant_scope_digest')}/"
            f"jobs/{_identifier(job_id, 'job_id')}/attempts/"
            f"{_identifier(attempt_id, 'attempt_id')}"
        )
        for raw in artifacts:
            if not isinstance(raw, Mapping):
                raise MlInternTrainingWorkerTransportError(
                    "invalid_worker_response", "worker artifact metadata is invalid"
                )
            _validate_artifact_metadata(raw)
            name = _artifact_name(raw.get("name"))
            expected_hash = str(raw.get("sha256") or "").lower()
            if not _SHA256.fullmatch(expected_hash):
                raise MlInternTrainingWorkerTransportError("invalid_worker_response", "worker artifact hash is invalid")
            size = int(raw["size_bytes"])
            response = self._request_stream("GET", f"/jobs/{job_id}/artifacts/{urllib.parse.quote(name, safe='/')}")
            declared_media = str(raw.get("media_type") or "application/octet-stream").split(";", 1)[0].lower()
            response_media = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if response_media != declared_media:
                raise MlInternTrainingWorkerTransportError(
                    "artifact_content_type_mismatch",
                    "worker artifact content type differs from its manifest",
                    retryable=False,
                )
            header_hash = str(response.headers.get("X-Artifact-SHA256") or "").lower()
            if header_hash and header_hash != expected_hash:
                raise MlInternTrainingWorkerTransportError(
                    "artifact_hash_mismatch",
                    "worker artifact header hash differs",
                    retryable=False,
                )
            stored = self._artifact_store.store_upload(
                response,
                destination_relative=(
                    f"{base}/adapter/{name}"
                    if job_type == "train_lora" and name not in {"training_manifest.json", "evaluation.json"}
                    else f"{base}/artifacts/{name}"
                ),
                filename=Path(name).name,
                media_type=declared_media,
                declared_size=size,
                expected_sha256=expected_hash,
                request_bytes_used=total,
            )
            total += stored.size_bytes
            admitted.append({"name": name, "sha256": stored.sha256, "size_bytes": stored.size_bytes})
        required = (
            {"eval_report.json", "evaluation.json", "evaluation_manifest.json"}
            if job_type == "evaluate_lora"
            else {"adapter_config.json", "adapter_model.safetensors", "training_manifest.json"}
        )
        present = {item["name"] for item in admitted}
        if not required.issubset(present):
            raise MlInternTrainingWorkerTransportError(
                "worker_artifact_incomplete",
                "worker result is missing required artifacts",
                retryable=False,
            )
        return admitted

    def _split_manifest(self, path: Path, partition: str) -> dict[str, Any]:
        resolved = Path(path).resolve(strict=True)
        try:
            relative = resolved.relative_to(self._dataset_root).as_posix()
        except ValueError as exc:
            raise MlInternTrainingWorkerTransportError(
                "dataset_boundary_violation", f"{partition} split is outside the shared dataset root", retryable=False
            ) from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise MlInternTrainingWorkerTransportError("dataset_missing", f"{partition} split is not a regular file")
        return {
            "relative_path": relative,
            "sha256": _file_sha256(resolved),
            "record_count": _jsonl_record_count(resolved),
        }

    def _request_json(self, method: str, suffix: str, body: Mapping[str, Any] | None) -> Mapping[str, Any]:
        response = self._request(method, suffix, body)
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            raise _invalid_worker_response("worker response must be JSON")
        raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise MlInternTrainingWorkerTransportError(
                "worker_response_too_large",
                "worker response exceeds its limit",
                retryable=False,
            )
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_non_finite_json_constant,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise _invalid_worker_response("worker returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise _invalid_worker_response("worker response must be an object")
        _validate_bounded_json(payload, field="worker response")
        if isinstance(payload.get("error"), Mapping):
            _closed_worker_object(
                payload,
                field="worker error response",
                allowed=frozenset({"contract_version", "status", "error"}),
            )
            if (
                set(payload) != {"contract_version", "status", "error"}
                or payload.get("contract_version") != WORKER_CONTRACT_VERSION
                or payload.get("status") != "failed"
            ):
                raise _invalid_worker_response("worker error response is invalid")
            error = payload["error"]
            _validate_worker_error(error)
            raise MlInternTrainingWorkerTransportError(
                str(error.get("code") or "worker_rejected"),
                str(error.get("message") or "training worker rejected the request"),
                retryable=bool(error.get("retryable", False)),
            )
        return payload

    def _request_stream(self, method: str, suffix: str) -> BinaryIO:
        return self._request(method, suffix, None)

    def _request(self, method: str, suffix: str, body: Mapping[str, Any] | None) -> Any:
        if not suffix.startswith("/") or ".." in suffix or "\\" in suffix:
            raise ValueError("worker path suffix is invalid")
        address = self._private_pinned_address()
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        url = urllib.parse.urlunsplit(("http", netloc, f"{_WORKER_BASE_PATH}{suffix}", "", ""))
        data = None
        if body is not None:
            data = json.dumps(dict(body), separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(data) > 2 * 1024 * 1024:
                raise MlInternTrainingWorkerTransportError("request_too_large", "worker request exceeds its limit")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json, application/octet-stream",
                "Content-Type": "application/json",
                "Host": self._parsed.netloc,
            },
        )
        try:
            return self._opener.open(request, timeout=self._connect_timeout)
        except urllib.error.HTTPError as exc:
            return exc
        except MlInternTrainingWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise MlInternTrainingWorkerTransportError(
                "training_worker_unavailable", "LoRA training worker is unavailable"
            ) from exc

    def _private_pinned_address(self) -> str:
        try:
            return pin_private_container_address(
                str(self._parsed.hostname or ""), int(self._parsed.port or 0), resolver=self._resolver
            )
        except PrivateContainerResolutionError as exc:
            raise MlInternTrainingWorkerTransportError(exc.reason_code, str(exc)) from exc


def normalize_training_worker_endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != _WORKER_BASE_PATH
    ):
        raise ValueError("LoRA training worker endpoint must be the explicit internal HTTP endpoint")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", _WORKER_BASE_PATH, "", ""))


def training_worker_port_from_environment(config: Mapping[str, Any]) -> HttpMlInternTrainingWorkerPort | None:
    endpoint = str(os.getenv("ANANTA_LORA_TRAINING_WORKER_URL", "")).strip()
    if not endpoint:
        return None
    allowed = tuple(
        item.strip() for item in str(os.getenv("ANANTA_LORA_TRAINING_ALLOWED_ENDPOINTS", "")).split(",") if item.strip()
    )
    if not allowed:
        raise RuntimeError("LoRA training worker URL is configured without an exact endpoint allowlist")
    token = _worker_token_from_environment()
    catalog_value = os.getenv("ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON")
    if catalog_value:
        try:
            catalog = json.loads(catalog_value)
        except ValueError as exc:
            raise RuntimeError("LoRA training model catalog JSON is invalid") from exc
    else:
        catalog = config.get("base_model_catalog") or {}
    if not isinstance(catalog, Mapping):
        raise RuntimeError("LoRA training model catalog must be an object")
    return HttpMlInternTrainingWorkerPort(
        endpoint=endpoint,
        allowed_endpoints=allowed,
        bearer_token=token,
        dataset_root=os.getenv("ANANTA_LORA_TRAINING_DATASET_ROOT")
        or config.get("dataset_root")
        or "data/training/lora",
        workspace_root=os.getenv("ANANTA_LORA_TRAINING_WORKSPACE_ROOT") or "project-workspaces/lora-training",
        artifact_root=config.get("artifact_root") or "artifacts/lora",
        model_catalog=catalog,  # type: ignore[arg-type]
        adapter_resolver=_registry_adapter_resolver(config),
        admitted_backends=tuple(
            item.strip().lower()
            for item in str(
                os.getenv("ANANTA_LORA_TRAINING_WORKER_BACKENDS")
                or os.getenv("ANANTA_LORA_TRAINING_DEFAULT_BACKEND")
                or "mock"
            ).split(",")
            if item.strip()
        ),
        resource_profile=str(os.getenv("ANANTA_LORA_TRAINING_RESOURCE_PROFILE") or "nvidia"),
        timeout_seconds=int(config.get("timeout_seconds") or 3600),
        max_artifact_bytes=int(config.get("max_adapter_bytes") or 2 * 1024 * 1024 * 1024),
    )


def _registry_adapter_resolver(config: Mapping[str, Any]) -> Callable[[str, str], Path]:
    runtime = config.get("lora_runtime") if isinstance(config.get("lora_runtime"), Mapping) else {}
    artifact_root = Path(str(config.get("artifact_root") or "artifacts/lora")).resolve()
    registry = MlInternAdapterRegistryService(
        str(runtime.get("adapter_registry_path") or artifact_root / "adapter_registry.json")
    )

    def resolve(adapter_id: str, tenant_scope_digest: str) -> Path:
        record = registry.get_by_scope_digest(adapter_id, tenant_scope_digest)
        if record is None:
            raise MlInternTrainingWorkerTransportError(
                "adapter_not_found", "adapter is not registered", retryable=False
            )
        raw = record.artifact_paths.get("adapter_dir") or record.artifact_paths.get("adapter_path")
        if not raw:
            raise MlInternTrainingWorkerTransportError(
                "adapter_artifact_missing", "registered adapter has no artifact", retryable=False
            )
        unresolved = Path(raw)
        candidate = (
            unresolved.resolve(strict=True)
            if unresolved.is_absolute()
            else (artifact_root / unresolved).resolve(strict=True)
        )
        try:
            candidate.relative_to(artifact_root)
        except ValueError as exc:
            raise MlInternTrainingWorkerTransportError(
                "adapter_boundary_violation",
                "registered adapter artifact is outside the configured artifact root",
                retryable=False,
            ) from exc
        return candidate

    return resolve


def _worker_token_from_environment() -> str:
    inline = str(os.getenv("ANANTA_LORA_TRAINING_TOKEN", "")).strip()
    path = str(os.getenv("ANANTA_LORA_TRAINING_TOKEN_FILE", "")).strip()
    if path:
        try:
            file_token = read_file_managed_token(
                path,
                description="LoRA training worker token file",
                min_bytes=24,
                max_bytes=16_384,
            )
        except FileCredentialConfigurationError as exc:
            raise RuntimeError(str(exc)) from exc
        if inline and inline != file_token:
            raise RuntimeError("inline and file-managed LoRA training worker tokens conflict")
        return file_token
    if len(inline) < 24:
        raise RuntimeError("LoRA training worker URL is configured without a valid bearer token")
    return inline


def _normalize_model_catalog(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for model_id, raw in value.items():
        key = str(model_id or "").strip()
        if not key or len(key) > 256 or not isinstance(raw, Mapping):
            raise ValueError("LoRA training model catalog contains an invalid model")
        relative = _safe_relative(str(raw.get("relative_path") or ""), "base model path")
        digest = str(raw.get("snapshot_hash") or "").strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ValueError("LoRA training model catalog contains an invalid snapshot hash")
        result[key] = {"relative_path": relative, "snapshot_hash": digest}
    if not result:
        raise ValueError("LoRA training model catalog must contain at least one model")
    return result


def _worker_configuration(spec: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(spec.get("hyperparameters") or {})
    max_steps = int(values.get("max_steps") or 100)
    evaluation_steps = int(values.get("evaluation_steps") or min(10, max_steps))
    return {
        "seed": int(values.get("seed") or 42),
        "max_steps": max_steps,
        "num_train_epochs": float(values.get("num_train_epochs") or 1.0),
        "learning_rate": float(values.get("learning_rate") or 2e-4),
        "train_batch_size": int(values.get("batch_size") or 1),
        "eval_batch_size": int(values.get("batch_size") or 1),
        "gradient_accumulation_steps": int(values.get("gradient_accumulation_steps") or 1),
        "eval_steps": evaluation_steps,
        "save_steps": evaluation_steps,
        "early_stopping_patience": int(values.get("early_stopping_patience") or 0),
        "lora_rank": int(values.get("lora_rank") or 16),
        "lora_alpha": int(values.get("lora_alpha") or 32),
        "lora_dropout": float(values.get("lora_dropout") or 0.05),
        "max_sequence_length": int(values.get("max_seq_length") or 2048),
        "quantization": "4bit" if bool(values.get("load_in_4bit", spec.get("method") == "qlora")) else "none",
        "gradient_checkpointing": True,
        "target_modules": list(values.get("target_modules") or ["q_proj", "k_proj", "v_proj", "o_proj"]),
    }


def _worker_exports(spec: Mapping[str, Any], *, backend: str) -> list[dict[str, str]]:
    raw = spec.get("exports")
    if raw is None:
        return []
    if backend != "unsloth":
        raise MlInternTrainingWorkerTransportError(
            "unsloth_export_backend_required",
            "post-training exports require the text Unsloth backend",
            retryable=False,
        )
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 8:
        raise MlInternTrainingWorkerTransportError(
            "unsloth_exports_invalid",
            "exports must be a non-empty array with at most eight entries",
            retryable=False,
        )
    exports: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping) or any(not isinstance(key, str) for key in item):
            raise MlInternTrainingWorkerTransportError(
                "unsloth_exports_invalid",
                "each export must be an object",
                retryable=False,
            )
        if set(item) - {"format", "quantization_method"}:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_exports_invalid",
                "export contains unknown fields",
                retryable=False,
            )
        export_format = str(item.get("format") or "").strip().lower()
        quantization = str(item.get("quantization_method") or "").strip().lower()
        if export_format not in {"adapter", "merged_16bit", "gguf"}:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_format_invalid",
                "export format is not supported",
                retryable=False,
            )
        if export_format == "gguf":
            if quantization not in {"q4_k_m", "q5_k_m", "q8_0"}:
                raise MlInternTrainingWorkerTransportError(
                    "unsloth_export_quantization_invalid",
                    "GGUF quantization_method is not supported",
                    retryable=False,
                )
        elif quantization:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_quantization_invalid",
                "quantization_method is only valid for GGUF exports",
                retryable=False,
            )
        identity = (export_format, quantization)
        if identity in identities:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_duplicate",
                "exports contain a duplicate format and quantization pair",
                retryable=False,
            )
        identities.add(identity)
        export = {"format": export_format}
        if quantization:
            export["quantization_method"] = quantization
        exports.append(export)
    if any(item["format"] != "adapter" for item in exports) and spec.get("allow_merge") is not True:
        raise MlInternTrainingWorkerTransportError(
            "merge_confirmation_required",
            "allow_merge=true is required for merged_16bit and GGUF exports",
            retryable=False,
        )
    return exports


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _invalid_worker_response(message: str) -> MlInternTrainingWorkerTransportError:
    return MlInternTrainingWorkerTransportError(
        "invalid_worker_response",
        message,
        retryable=False,
    )


def _validate_bounded_json(
    value: Any,
    *,
    field: str,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    """Validate the JSON data model and reject non-finite or resource-heavy trees."""

    remaining = budget if budget is not None else [10_000]
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 12:
        raise _invalid_worker_response(f"{field} exceeds its structural bound")
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > 65_536:
            raise _invalid_worker_response(f"{field} contains an oversized string")
        return
    if isinstance(value, int):
        if abs(value) > 2**255 - 1:
            raise _invalid_worker_response(f"{field} contains an out-of-range integer")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid_worker_response(f"{field} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise _invalid_worker_response(f"{field} contains too many fields")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 512:
                raise _invalid_worker_response(f"{field} contains an invalid field name")
            _validate_bounded_json(child, field=f"{field}.{key}", depth=depth + 1, budget=remaining)
        return
    if isinstance(value, list):
        if len(value) > 1_000:
            raise _invalid_worker_response(f"{field} contains too many items")
        for index, child in enumerate(value):
            _validate_bounded_json(child, field=f"{field}[{index}]", depth=depth + 1, budget=remaining)
        return
    raise _invalid_worker_response(f"{field} contains a non-JSON value")


def _closed_worker_object(value: Mapping[str, Any], *, field: str, allowed: frozenset[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _invalid_worker_response(f"{field} contains unknown fields: {', '.join(unknown[:10])}")


def _validate_worker_error(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker error",
        allowed=frozenset({"code", "message", "retryable"}),
    )
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, str) or not _IDENTIFIER.fullmatch(code) or len(code) > 128:
        raise _invalid_worker_response("worker error code is invalid")
    if not isinstance(message, str) or not message.strip() or len(message) > 512:
        raise _invalid_worker_response("worker error message is invalid")
    if not isinstance(value.get("retryable"), bool):
        raise _invalid_worker_response("worker error retryable flag is invalid")


def _validate_artifact_metadata(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker artifact metadata",
        allowed=frozenset({"name", "sha256", "size_bytes", "media_type"}),
    )
    _artifact_name(value.get("name"))
    if not isinstance(value.get("sha256"), str) or not _SHA256.fullmatch(str(value["sha256"])):
        raise _invalid_worker_response("worker artifact hash is invalid")
    size = value.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 2**63 - 1:
        raise _invalid_worker_response("worker artifact size is invalid")
    media_type = value.get("media_type")
    if (
        not isinstance(media_type, str)
        or not 1 <= len(media_type) <= 128
        or "/" not in media_type
        or any(character.isspace() for character in media_type)
    ):
        raise _invalid_worker_response("worker artifact media type is invalid")


def _validate_resume_checkpoint(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker resume checkpoint",
        allowed=frozenset({"relative_path", "binding"}),
    )
    try:
        _safe_relative(str(value.get("relative_path") or ""), "worker checkpoint path")
    except ValueError as exc:
        raise _invalid_worker_response("worker checkpoint path is invalid") from exc
    binding = value.get("binding")
    if not isinstance(binding, Mapping):
        raise _invalid_worker_response("worker checkpoint binding is invalid")
    allowed = frozenset(
        {
            "job_id",
            "source_attempt_id",
            "base_model_hash",
            "dataset_hash",
            "configuration_hash",
            "checkpoint_sha256",
        }
    )
    _closed_worker_object(binding, field="worker checkpoint binding", allowed=allowed)
    for key in ("job_id", "source_attempt_id"):
        if not isinstance(binding.get(key), str) or not _IDENTIFIER.fullmatch(str(binding[key])):
            raise _invalid_worker_response("worker checkpoint identity binding is invalid")
    for key in ("base_model_hash", "dataset_hash", "configuration_hash", "checkpoint_sha256"):
        if not isinstance(binding.get(key), str) or not _SHA256.fullmatch(str(binding[key])):
            raise _invalid_worker_response("worker checkpoint hash binding is invalid")


def _validate_worker_status(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker status")
    _closed_worker_object(value, field="worker status", allowed=_WORKER_STATUS_FIELDS)
    if set(value) != _WORKER_STATUS_FIELDS:
        raise _invalid_worker_response("worker status is missing required result fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker status contract version is invalid")
    state = value.get("status")
    if state not in {"queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"}:
        raise _invalid_worker_response("worker status value is invalid")
    for key in ("job_id", "attempt_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker status {key} is invalid")
    fencing_token = value.get("fencing_token")
    if (
        isinstance(fencing_token, bool)
        or not isinstance(fencing_token, int)
        or not 1 <= fencing_token <= 2**255 - 1
    ):
        raise _invalid_worker_response("worker status fencing token is invalid")
    if not isinstance(value.get("correlation_id"), str) or not _IDENTIFIER.fullmatch(str(value["correlation_id"])):
        raise _invalid_worker_response("worker status correlation ID is invalid")
    if value.get("job_type") is not None and value.get("job_type") not in {
        "train_lora",
        "evaluate_existing_adapter",
    }:
        raise _invalid_worker_response("worker status job type is invalid")
    if value.get("backend") is not None and (
        not isinstance(value.get("backend"), str) or not _IDENTIFIER.fullmatch(str(value["backend"]))
    ):
        raise _invalid_worker_response("worker status backend is invalid")
    for key in ("created_at", "updated_at", "heartbeat_at"):
        timestamp = value.get(key)
        if timestamp is not None and (
            isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or float(timestamp) < 0
        ):
            raise _invalid_worker_response(f"worker status {key} is invalid")
    progress = value.get("progress")
    if progress is not None:
        if not isinstance(progress, Mapping):
            raise _invalid_worker_response("worker status progress is invalid")
        _closed_worker_object(
            progress,
            field="worker status progress",
            allowed=_WORKER_EVENT_PAYLOAD_FIELDS["progress"],
        )
        if progress:
            _validate_event_payload("progress", progress)
    metrics = value.get("metrics")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise _invalid_worker_response("worker status metrics are invalid")
    artifacts = value.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list) or len(artifacts) > 64:
            raise _invalid_worker_response("worker status artifacts are invalid")
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise _invalid_worker_response("worker status artifact metadata is invalid")
            _validate_artifact_metadata(artifact)
    checkpoint = value.get("resume_checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, Mapping):
            raise _invalid_worker_response("worker status resume checkpoint is invalid")
        _validate_resume_checkpoint(checkpoint)
    cancel_mode = value.get("cancel_mode")
    if cancel_mode is not None and cancel_mode not in {"graceful", "forced"}:
        raise _invalid_worker_response("worker status cancel mode is invalid")
    error = value.get("error")
    if error is not None:
        if not isinstance(error, Mapping):
            raise _invalid_worker_response("worker status error is invalid")
        _validate_worker_error(error)
    if state == "failed" and error is None:
        raise _invalid_worker_response("worker failure status is missing its structured error")
    if state == "succeeded" and error is not None:
        raise _invalid_worker_response("worker success status contains an error")
    if state == "cancelled" and cancel_mode not in {"graceful", "forced"}:
        raise _invalid_worker_response("worker cancellation status is missing its cancel mode")


def _validate_worker_event_page(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker event page")
    _closed_worker_object(
        value,
        field="worker event page",
        allowed=frozenset({"contract_version", "job_id", "attempt_id", "events", "next_sequence"}),
    )
    if set(value) != {"contract_version", "job_id", "attempt_id", "events", "next_sequence"}:
        raise _invalid_worker_response("worker event page is missing required fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker event page contract version is invalid")
    for key in ("job_id", "attempt_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker event page {key} is invalid")
    events = value.get("events")
    next_sequence = value.get("next_sequence")
    if not isinstance(events, list) or len(events) > 1_000:
        raise _invalid_worker_response("worker event page is invalid")
    if isinstance(next_sequence, bool) or not isinstance(next_sequence, int) or next_sequence < 0:
        raise _invalid_worker_response("worker event cursor is invalid")
    previous = -1
    for event in events:
        if not isinstance(event, Mapping):
            raise _invalid_worker_response("worker event is invalid")
        _validate_worker_event(event)
        sequence = int(event["sequence"])
        if sequence <= previous or sequence > next_sequence:
            raise _invalid_worker_response("worker event sequence is not monotone")
        previous = sequence
    if events and previous != next_sequence:
        raise _invalid_worker_response("worker event cursor does not match its last event")


def _validate_worker_event(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker event")
    _closed_worker_object(value, field="worker event", allowed=_WORKER_EVENT_FIELDS)
    if set(value) != _WORKER_EVENT_FIELDS:
        raise _invalid_worker_response("worker event is missing required fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker event contract version is invalid")
    for key in ("job_id", "attempt_id", "correlation_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker event {key} is invalid")
    fencing_token = value.get("fencing_token")
    if (
        isinstance(fencing_token, bool)
        or not isinstance(fencing_token, int)
        or not 1 <= fencing_token <= 2**255 - 1
    ):
        raise _invalid_worker_response("worker event fencing token is invalid")
    sequence = value.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise _invalid_worker_response("worker event sequence is invalid")
    timestamp = value.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or float(timestamp) < 0:
        raise _invalid_worker_response("worker event timestamp is invalid")
    event_type = value.get("type")
    allowed_payload = _WORKER_EVENT_PAYLOAD_FIELDS.get(str(event_type))
    if allowed_payload is None:
        raise _invalid_worker_response("worker event type is invalid")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise _invalid_worker_response("worker event payload is invalid")
    _closed_worker_object(payload, field="worker event payload", allowed=allowed_payload)
    _validate_event_payload(str(event_type), payload)


def _validate_event_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    if event_type == "artifact":
        _validate_artifact_metadata(payload)
        return
    if event_type == "accepted":
        backend = payload.get("backend")
        if not isinstance(backend, str) or not _IDENTIFIER.fullmatch(backend):
            raise _invalid_worker_response("worker accepted event backend is invalid")
        return
    if event_type == "status":
        if payload.get("status") not in {
            "queued",
            "running",
            "cancel_requested",
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise _invalid_worker_response("worker status event state is invalid")
        reason = payload.get("reason_code")
        if not isinstance(reason, str) or len(reason) > 128:
            raise _invalid_worker_response("worker status event reason is invalid")
        if not isinstance(payload.get("retryable"), bool):
            raise _invalid_worker_response("worker status event retryable flag is invalid")
        return
    if event_type == "phase":
        phase = payload.get("phase")
        if not isinstance(phase, str) or not phase or len(phase) > 64:
            raise _invalid_worker_response("worker phase event is invalid")
        step = payload.get("step")
        if step is not None and (
            isinstance(step, bool)
            or not isinstance(step, int)
            or not 0 <= step <= 10_000_000
        ):
            raise _invalid_worker_response("worker phase event step is invalid")
        modality = payload.get("modality")
        if modality is not None and modality not in _EVENT_MODALITIES:
            raise _invalid_worker_response("worker phase event modality is invalid")
        return
    if event_type == "resource_admission":
        required_fields = _RESOURCE_ADMISSION_PAYLOAD_FIELDS.difference({"reason_code"})
        if not required_fields.issubset(payload):
            raise _invalid_worker_response("worker resource admission event is missing required fields")
        profile = payload.get("profile")
        if not isinstance(profile, str) or not profile or len(profile) > 64:
            raise _invalid_worker_response("worker resource admission profile is invalid")
        if not isinstance(payload.get("admitted"), bool) or not isinstance(payload.get("estimate_only"), bool):
            raise _invalid_worker_response("worker resource admission flags are invalid")
        for field_name in ("estimated_peak_bytes", "reserve_bytes"):
            value = payload.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                raise _invalid_worker_response(f"worker resource admission {field_name} is invalid")
        usable_bytes = payload.get("usable_bytes")
        if usable_bytes is not None and (
            isinstance(usable_bytes, bool)
            or not isinstance(usable_bytes, int)
            or not 0 <= usable_bytes <= 2**63 - 1
        ):
            raise _invalid_worker_response("worker resource admission usable_bytes is invalid")
        assumptions = payload.get("assumptions")
        if (
            not isinstance(assumptions, list)
            or not 1 <= len(assumptions) <= 16
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in assumptions)
        ):
            raise _invalid_worker_response("worker resource admission assumptions are invalid")
        if payload.get("reason_code") not in {None, "vram_admission_admitted"}:
            raise _invalid_worker_response("worker resource admission reason is invalid")
        return
    if event_type == "checkpoint":
        step = payload.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise _invalid_worker_response("worker checkpoint event step is invalid")
        _artifact_name(payload.get("name"))
        digest = payload.get("sha256")
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise _invalid_worker_response("worker checkpoint event hash is invalid")
        return
    if event_type == "progress":
        step = payload.get("step")
        max_steps = payload.get("max_steps")
        if (
            isinstance(step, bool)
            or not isinstance(step, int)
            or isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or not 0 <= step <= max_steps
            or max_steps < 1
        ):
            raise _invalid_worker_response("worker progress event bounds are invalid")
        for key in (
            "epoch",
            "loss",
            "eval_loss",
            "learning_rate",
            "tokens_per_second",
            "gpu_utilization_percent",
            "vram_used_bytes",
        ):
            number = payload.get(key)
            if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))):
                raise _invalid_worker_response(f"worker progress event {key} is invalid")
        telemetry = payload.get("telemetry")
        if telemetry is not None:
            if not isinstance(telemetry, Mapping):
                raise _invalid_worker_response("worker progress telemetry is invalid")
            try:
                validate_progress_telemetry(telemetry)
            except UnslothWorkerCapabilityContractError as exc:
                raise _invalid_worker_response("worker progress telemetry is invalid") from exc


def _project_worker_event(event: Mapping[str, Any]) -> dict[str, Any]:
    _validate_worker_event(event)
    payload = dict(event.get("payload") or {}) if isinstance(event.get("payload"), Mapping) else {}
    event_type = str(event.get("type") or "progress")
    projected: dict[str, Any] = {"event_id": event.get("sequence"), "type": event_type}
    if event_type == "progress":
        step = int(payload.get("step") or 0)
        max_steps = int(payload.get("max_steps") or 0)
        projected.update(
            {
                "current_step": step,
                "max_steps": max_steps,
                "progress_percent": round(step / max_steps * 100, 4) if max_steps else 0,
                "epoch": payload.get("epoch"),
                "train_loss": payload.get("loss"),
                "eval_loss": payload.get("eval_loss"),
                "learning_rate": payload.get("learning_rate"),
                "telemetry": (
                    validate_progress_telemetry(payload["telemetry"])
                    if isinstance(payload.get("telemetry"), Mapping)
                    else progress_telemetry(payload)
                ),
                "phase": "training",
            }
        )
        for metric_name, state in projected["telemetry"].items():
            if state["status"] == "available":
                projected[metric_name] = state["value"]
    elif event_type == "phase":
        projected["phase"] = payload.get("phase")
        if payload.get("step") is not None:
            projected["current_step"] = payload.get("step")
    elif event_type == "status":
        projected.update(
            {
                "status": payload.get("status"),
                "reason_code": payload.get("reason_code"),
                "retryable": payload.get("retryable"),
            }
        )
    elif event_type == "checkpoint":
        projected.update({"current_step": payload.get("step"), "checkpoint_ref": payload.get("name")})
    return projected


def _validate_worker_correlation(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    attempt_id: str,
    fencing_token: int | None,
    correlation_id: str | None,
) -> None:
    if str(payload.get("contract_version") or "") != WORKER_CONTRACT_VERSION:
        raise MlInternTrainingWorkerTransportError(
            "worker_contract_mismatch",
            "worker response uses an unexpected contract version",
            retryable=False,
        )
    if str(payload.get("job_id") or "") != job_id or str(payload.get("attempt_id") or "") != attempt_id:
        raise MlInternTrainingWorkerTransportError(
            "worker_correlation_mismatch",
            "worker response does not match the admitted job attempt",
            retryable=False,
        )
    if fencing_token is not None and int(payload.get("fencing_token") or 0) != fencing_token:
        raise MlInternTrainingWorkerTransportError(
            "stale_worker_fence",
            "worker response carries a stale fencing token",
            retryable=False,
        )
    if correlation_id is not None and str(payload.get("correlation_id") or "") != correlation_id:
        raise MlInternTrainingWorkerTransportError(
            "worker_correlation_mismatch",
            "worker response does not match the admitted correlation ID",
            retryable=False,
        )


def _existing_root(value: str | Path, name: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{name} must be an existing non-symlink directory")
    return path


def _writable_root(value: str | Path, name: str) -> Path:
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return _existing_root(path, name)


def _safe_relative(value: str, name: str) -> str:
    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValueError(f"{name} must be a safe relative path")
    return candidate.as_posix()


def _identifier(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise MlInternTrainingWorkerTransportError("invalid_identifier", f"{name} is invalid", retryable=False)
    return normalized


def _artifact_name(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not _ARTIFACT_NAME.fullmatch(normalized)
        or normalized.startswith("/")
        or ".." in normalized.split("/")
        or "//" in normalized
    ):
        raise MlInternTrainingWorkerTransportError(
            "invalid_worker_response",
            "worker artifact name is invalid",
            retryable=False,
        )
    return normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_symlink():
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains a symbolic link", retryable=False
        )
    if path.is_file():
        return _file_sha256(path)
    if not path.is_dir():
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact is not a regular tree", retryable=False
        )
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains a symbolic link", retryable=False
        )
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains an unsupported entry", retryable=False
        )
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact tree is empty", retryable=False
        )
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _jsonl_record_count(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                count += 1
                if count > 10_000_000:
                    raise MlInternTrainingWorkerTransportError("dataset_too_large", "dataset exceeds record limit")
    if count < 1:
        raise MlInternTrainingWorkerTransportError("dataset_empty", "dataset split is empty", retryable=False)
    return count
