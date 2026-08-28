"""Secure Hub transport for the isolated LoRA-training worker.

The Hub owns admission and polling.  This adapter only translates an admitted
Hub job into the worker's versioned execution contract; it never imports an ML
runtime and it refuses redirects, public addresses and non-allowlisted URLs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_training_artifact_binding import (
    MlInternTrainingArtifactBinding,
)
from agent.services.ml_intern_training_worker_contract import (
    _SHA256,
    _TRAINING_BACKENDS,
    _WORKER_BASE_PATH,
    WORKER_CONTRACT_VERSION,
    MlInternTrainingWorkerTransportError,
)
from agent.services.ml_intern_training_worker_support import (
    _artifact_name,
    _closed_worker_object,
    _existing_root,
    _file_sha256,
    _identifier,
    _invalid_worker_response,
    _jsonl_record_count,
    _normalize_model_catalog,
    _path_sha256,
    _project_worker_event,
    _reject_non_finite_json_constant,
    _validate_artifact_metadata,
    _validate_bounded_json,
    _validate_worker_correlation,
    _validate_worker_error,
    _validate_worker_event,
    _validate_worker_event_page,
    _validate_worker_status,
    _worker_configuration,
    _worker_exports,
    _worker_token_from_environment,
    _writable_root,
)
from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    validate_worker_capability_probe,
)

__all__ = [
    "WORKER_CONTRACT_VERSION",
    "HttpMlInternTrainingWorkerPort",
    "MlInternTrainingWorkerTransportError",
    "_NoRedirectHandler",
    "_path_sha256",
    "_validate_artifact_metadata",
    "_validate_worker_event",
    "_validate_worker_status",
    "_worker_exports",
    "normalize_training_worker_endpoint",
    "training_worker_port_from_environment",
]


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
            "needle",
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
        tenant_storage_key = str(spec.get("_tenant_storage_key") or tenant_scope_digest).strip().lower()
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
            workspace_ref = f"tenants/{tenant_scope_digest}/jobs/{job_id}/attempts/{attempt_id}/workspace"
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
            f"tenants/{common['tenant_scope_digest']}/jobs/{job_id}/attempts/{common['attempt_id']}/workspace"
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
            if (
                not isinstance(error, Mapping)
                or not str(error.get("code") or "").strip()
                or not str(error.get("message") or "").strip()
            ):
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
            backend=str(spec.get("backend") or ""),
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
            "_artifact_storage_binding": MlInternTrainingArtifactBinding(
                tenant_scope_digest=str(spec.get("_tenant_scope_digest") or ""),
                job_id=job_id,
                attempt_id=str(status["attempt_id"]),
            ).to_mapping(),
        }

    def _download_artifacts(
        self,
        job_id: str,
        artifacts: Sequence[Any],
        *,
        job_type: str,
        backend: str,
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
            else {"adapter.pkl", "training_manifest.json"}
            if backend == "needle"
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
