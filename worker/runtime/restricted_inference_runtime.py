"""Worker-side handler for the restricted-inference wire contract.

The handler accepts only a hub-created envelope, requires an admitted model
snapshot, and delegates one operation to an injected executor.  It contains no
queue or worker-routing logic: orchestration remains in the hub.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Mapping, Protocol, cast

from agent.services.restricted_inference_contract import (
    RestrictedInferenceContractError,
    RestrictedInferenceError,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    RestrictedInferenceStatus,
    validate_response_for_request,
)
from agent.services.restricted_inference_model_manifest import (
    ModelManifestValidationError,
    VerifiedModelSnapshot,
)


class RestrictedInferenceSnapshotAdmission(Protocol):
    """Resolve only snapshots that have passed manifest verification."""

    def admit(self, manifest_id: str) -> VerifiedModelSnapshot: ...


class RestrictedInferenceOperationExecutor(Protocol):
    """Worker-owned operation executor; model adapters implement this seam."""

    def execute(
        self,
        request: RestrictedInferenceRequest,
        snapshot: VerifiedModelSnapshot,
    ) -> Mapping[str, Any]: ...


class RestrictedInferenceWorkerRuntime:
    def __init__(
        self,
        *,
        snapshot_admission: RestrictedInferenceSnapshotAdmission,
        executor: RestrictedInferenceOperationExecutor,
        epoch_ms: Callable[[], int] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        require_run_id: bool = False,
    ) -> None:
        self._snapshot_admission = snapshot_admission
        self._executor = executor
        self._epoch_ms = epoch_ms or (lambda: time.time_ns() // 1_000_000)
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._require_run_id = require_run_id

    def status(self) -> dict[str, Any]:
        read_status = getattr(self._executor, "status", None)
        status = dict(read_status()) if callable(read_status) else {"models": [], "resources": {}, "cache_entries": 0}
        read_catalog = getattr(self._snapshot_admission, "capability_catalog", None)
        capabilities = list(read_catalog()) if callable(read_catalog) else []
        lifecycle = {
            str(item.get("manifest_digest") or ""): dict(item)
            for item in list(cast(Iterable[Any], status.get("models") or ()))
            if isinstance(item, Mapping)
        }
        for capability in capabilities:
            current = lifecycle.get(str(capability.get("manifest_digest") or ""))
            if current is None:
                continue
            state = str(current.get("state") or "")
            if state == "degraded":
                capability["status"] = "degraded"
            elif state in {"failed", "unavailable"}:
                capability["status"] = "unavailable"
            extension = capability.get("extensions")
            restricted = extension.get("restricted_inference") if isinstance(extension, dict) else None
            if isinstance(restricted, dict):
                restricted["lifecycle_state"] = state
                restricted["loaded_device"] = current.get("loaded_device") or None
                restricted["reason_code"] = current.get("failure_code") or restricted.get("reason_code")
        return {**status, "capability_catalog": capabilities}

    def unload(self, manifest_digest: str) -> bool:
        unload = getattr(self._executor, "unload", None)
        if not callable(unload):
            return False
        return bool(unload(manifest_digest))

    def load(self, manifest_id: str, *, deadline_epoch_ms: int) -> dict[str, Any]:
        snapshot = self._snapshot_admission.admit(manifest_id)
        load = getattr(self._executor, "load", None)
        if not callable(load):
            raise RuntimeError("restricted inference executor does not support explicit load")
        return dict(load(snapshot, deadline_epoch_ms=deadline_epoch_ms))

    def configuration(self) -> dict[str, Any]:
        read = getattr(self._executor, "configuration", None)
        if not callable(read):
            return {
                "schema_version": "ananta.restricted-runtime-config.v1",
                "version": 1,
                "mutable": {},
                "fixed": {
                    "downloads_allowed": False,
                    "generation_allowed": False,
                    "local_snapshots_only": True,
                    "trust_remote_code": False,
                },
            }
        return dict(read())

    def update_configuration(self, delta: Mapping[str, Any], *, expected_version: int) -> dict[str, Any]:
        update = getattr(self._executor, "update_configuration", None)
        if not callable(update):
            raise RuntimeError("restricted inference executor configuration is immutable")
        return dict(update(delta, expected_version=expected_version))

    def cache_gc(self) -> int:
        clear = getattr(self._executor, "cache_gc", None)
        return int(clear()) if callable(clear) else 0

    def handle(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one already-delegated job; never enqueue or route work."""

        request = RestrictedInferenceRequest.from_dict(envelope)
        if self._require_run_id and not request.run_id:
            return self._failure(request, "run_id_required", "production requests require run correlation")
        if request.deadline_epoch_ms <= self._epoch_ms():
            return self._failure(request, "timeout", "restricted inference deadline expired", retryable=True)

        try:
            snapshot = self._snapshot_admission.admit(request.model_manifest_id)
        except ModelManifestValidationError as exc:
            return self._failure(request, exc.reason_code, "model snapshot admission failed")
        except KeyError:
            return self._failure(request, "manifest_unavailable", "model manifest is unavailable", retryable=True)

        if snapshot.manifest_id != request.model_manifest_id:
            return self._failure(request, "manifest_mismatch", "admitted manifest does not match the request")

        started_ns = self._monotonic_ns()
        try:
            raw_result = self._executor.execute(request, snapshot)
            if request.deadline_epoch_ms <= self._epoch_ms():
                return self._failure(request, "timeout", "restricted inference deadline expired", retryable=True)
            if not isinstance(raw_result, Mapping):
                raise RestrictedInferenceContractError(
                    "invalid_worker_result",
                    "restricted inference executor returned a non-object result",
                )
            result = dict(raw_result)
            self._bind_provenance(result, snapshot)
            result["latency_ms"] = max(0.0, (self._monotonic_ns() - started_ns) / 1_000_000)
            response = RestrictedInferenceResponse(
                request_id=request.request_id,
                task_id=request.task_id,
                operation=request.operation,
                status=RestrictedInferenceStatus.SUCCEEDED,
                result=result,
                no_generation=True,
            )
            validate_response_for_request(request, response)
        except RestrictedInferenceContractError as exc:
            return self._failure(request, exc.reason_code, "worker result violated restricted contract")
        except Exception as exc:
            reason_code = str(getattr(exc, "reason_code", "") or "model_error")
            retryable = bool(getattr(exc, "retryable", False))
            return self._failure(
                request,
                reason_code,
                "restricted inference execution failed",
                retryable=retryable,
            )
        return response.to_dict()

    @staticmethod
    def _bind_provenance(result: dict[str, Any], snapshot: VerifiedModelSnapshot) -> None:
        expected = {
            "engine": snapshot.engine,
            "manifest_digest": snapshot.manifest_digest,
            "model_id": snapshot.model_id,
        }
        for key, expected_value in expected.items():
            reported = result.get(key)
            if reported not in (None, "", expected_value):
                raise RestrictedInferenceContractError(
                    "result_provenance_mismatch",
                    f"worker result reported a mismatching {key}",
                )
            result[key] = expected_value

    @staticmethod
    def _failure(
        request: RestrictedInferenceRequest,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        return RestrictedInferenceResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            operation=request.operation,
            status=RestrictedInferenceStatus.FAILED,
            error=RestrictedInferenceError(code=code, message=message, retryable=retryable),
            no_generation=True,
        ).to_dict()
