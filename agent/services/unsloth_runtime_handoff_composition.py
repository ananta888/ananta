"""Production Hub composition for provider-neutral Unsloth runtime handoff."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.common.audit import log_audit
from agent.repository import task_repo
from agent.services.integration_registry_service import (
    IntegrationRegistryService,
    get_integration_registry_service,
)
from agent.services.ml_intern_adapter_export_service import (
    AdapterExportError,
    MlInternAdapterExportService,
)
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
)
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import TaskRuntimeService
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationError,
)
from agent.services.unsloth_runtime_endpoint_registry_service import (
    RuntimeEndpointRegistryError,
    RuntimeEndpointRegistryPort,
    SqliteRuntimeEndpointRegistry,
)
from agent.services.unsloth_runtime_handoff_service import (
    RuntimeArtifact,
    RuntimeHandoffError,
    RuntimeHandoffRequest,
    UnslothRuntimeHandoffService,
    validate_runtime_promotion_binding,
)
from agent.services.unsloth_storage_contracts import StorageReferencePort


class _RuntimeHandoffAudit:
    def record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        subject_id: str,
        details: Mapping[str, object],
    ) -> None:
        log_audit(
            event_type,
            {
                "tenant_scope_digest": _sha256(tenant_id),
                "subject_id": subject_id,
                **dict(details),
            },
        )


class RuntimeHandoffTaskHandler:
    """Hub-local handler that only advances the endpoint registry."""

    def __init__(self, endpoints: RuntimeEndpointRegistryPort) -> None:
        self._endpoints = endpoints

    def handle(
        self,
        *,
        task_id: str,
        tenant_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        revision = self._endpoints.apply_handoff(
            tenant_id=tenant_id,
            endpoint_id=str(payload.get("endpoint_id") or ""),
            expected_revision=int(payload.get("expected_endpoint_revision")),
            task_id=task_id,
            idempotency_key=idempotency_key,
            manifest=payload,
        )
        return revision.public_summary()


class HubRuntimeHandoffTaskController:
    """Records a real Hub task and executes its provider-neutral local handler."""

    def __init__(
        self,
        *,
        handler: RuntimeHandoffTaskHandler,
        queue=None,
        repository=None,
        runtime=None,
    ) -> None:
        self._handler = handler
        self._queue = queue or get_task_queue_service()
        self._repository = repository or task_repo
        self._runtime = runtime or TaskRuntimeService()

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        if task_type != "ml.runtime.artifact_handoff":
            raise RuntimeHandoffError(
                "runtime_handoff_task_type_invalid",
                "The task controller accepts only runtime artifact handoffs.",
            )
        encoded = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload_digest = _sha256(encoded)
        task_identity = _sha256(
            f"{tenant_id}\0{idempotency_key}"
        )
        task_id = f"ush-{task_identity[:32]}"
        existing = self._repository.get_by_id(task_id)
        if existing is not None:
            context = dict(
                getattr(existing, "worker_execution_context", None) or {}
            )
            stored = dict(context.get("runtime_handoff") or {})
            if (
                stored.get("tenant_id") != tenant_id
                or stored.get("payload_digest") != payload_digest
            ):
                raise RuntimeHandoffError(
                    "runtime_handoff_task_conflict",
                    "The Hub task ID is bound to another handoff.",
                )
            if str(getattr(existing, "status", "") or "") == "completed":
                return task_id
        else:
            self._queue.ingest_task(
                task_id=task_id,
                status="in_progress",
                title="Apply provider-neutral runtime endpoint handoff",
                description=(
                    "Validate and persist one immutable runtime endpoint "
                    "revision in the Hub."
                ),
                priority="high",
                created_by="system:unsloth-runtime-handoff",
                source="system",
                tags=["unsloth", "runtime-handoff", "hub-local"],
                event_type="runtime_handoff_task_created",
                event_details={
                    "endpoint_id": str(payload.get("endpoint_id") or ""),
                    "artifact_id": str(
                        (payload.get("artifact") or {}).get("artifact_id")  # type: ignore[union-attr]
                        or ""
                    ),
                    "payload_digest": payload_digest,
                },
                extra_fields={
                    "task_kind": task_type,
                    "required_capabilities": [],
                    "worker_execution_context": {
                        "schema": "ananta.runtime-handoff-task.v1",
                        "runtime_path": "hub_local_endpoint_registry",
                        "runtime_handoff": {
                            "tenant_id": tenant_id,
                            "payload_digest": payload_digest,
                            "payload": dict(payload),
                        },
                    },
                },
            )
        try:
            result = self._handler.handle(
                task_id=task_id,
                tenant_id=tenant_id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self._runtime.update_local_task_status(
                task_id,
                "failed",
                error="runtime_handoff_handler_failed",
                status_reason_code=str(
                    getattr(exc, "reason_code", "runtime_handoff_handler_failed")
                ),
                event_type="runtime_handoff_task_failed",
                event_actor="system:unsloth-runtime-handoff",
            )
            raise
        self._runtime.update_local_task_status(
            task_id,
            "completed",
            verification_status={"runtime_handoff": dict(result)},
            event_type="runtime_handoff_task_completed",
            event_actor="system:unsloth-runtime-handoff",
            event_details={
                "endpoint_id": result.get("endpoint_id"),
                "endpoint_revision": result.get("endpoint_revision"),
                "artifact_sha256": result.get("artifact_sha256"),
            },
        )
        return task_id


class UnslothRuntimeHandoffMutationExecutor:
    """Operation-payload executor for the closed Unsloth command boundary."""

    def __init__(
        self,
        *,
        handoff: UnslothRuntimeHandoffService,
        endpoints: RuntimeEndpointRegistryPort,
        export_service: MlInternAdapterExportService,
        adapter_registry: MlInternAdapterRegistryService,
        integrations: IntegrationRegistryService,
    ) -> None:
        self._handoff = handoff
        self._endpoints = endpoints
        self._exports = export_service
        self._adapters = adapter_registry
        self._integrations = integrations

    def preview(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
    ) -> Mapping[str, Any]:
        del principal, resource_id, reason
        raise UnslothMutationError(
            "runtime_handoff_contract_required",
            "Runtime handoff requires its operation-specific contract.",
        )

    def execute(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del principal, resource_id, reason, idempotency_key
        raise UnslothMutationError(
            "runtime_handoff_contract_required",
            "Runtime handoff requires its operation-specific contract.",
        )

    def preview_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        plan = self._plan(
            principal=principal,
            resource_id=resource_id,
            reason=reason,
            operation_payload=operation_payload,
        )
        payload = json.loads(plan.payload_json)
        return {
            "endpoint_id": payload["endpoint_id"],
            "expected_endpoint_revision": payload[
                "expected_endpoint_revision"
            ],
            "promoted_artifact_id": payload["artifact"]["artifact_id"],
            "promoted_artifact_sha256": payload["artifact"][
                "artifact_sha256"
            ],
            "provider_id": payload["provider_descriptor"]["provider_id"],
            "model_id": payload["provider_descriptor"]["model_id"],
            "api_capabilities": payload["api_capabilities"],
            "fallback": None,
            "manifest_sha256": _sha256(plan.payload_json),
        }

    def execute_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        plan = self._plan(
            principal=principal,
            resource_id=resource_id,
            reason=reason,
            operation_payload=operation_payload,
        )
        try:
            task_id = self._handoff.submit(
                plan,
                confirmation_digest=plan.confirmation_digest,
                idempotency_key=idempotency_key,
            )
            endpoint = self._endpoints.resolve_for_invocation(
                tenant_id=principal.tenant_id,
                endpoint_id=str(operation_payload["endpoint_descriptor"]["endpoint_id"]),
                required_capability=next(
                    capability
                    for capability, enabled in dict(
                        operation_payload["provider_descriptor"]["capabilities"]
                    ).items()
                    if enabled is True
                ),
            )
        except (RuntimeHandoffError, RuntimeEndpointRegistryError) as exc:
            raise _mutation_error(exc) from exc
        return {
            "task_id": task_id,
            **{
                key: value
                for key, value in endpoint.items()
                if key
                in {
                    "endpoint_id",
                    "endpoint_revision",
                    "state",
                    "provider_id",
                    "provider_type",
                    "model_id",
                    "artifact_id",
                    "artifact_sha256",
                    "api_capabilities",
                    "fallback",
                }
            },
        }

    def _plan(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        operation_payload: Mapping[str, Any],
    ):
        record = self._adapters.get(
            resource_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
        if (
            record is None
            or record.status != "approved"
            or not record.provenance_verified
            or not record.promotion_history
        ):
            raise UnslothMutationError(
                "runtime_handoff_artifact_not_promoted",
                "Only a promoted, provenance-verified adapter may be handed off.",
                status_code=409,
            )
        artifact_id = str(operation_payload["promoted_artifact_id"])
        artifact_sha256 = str(operation_payload["promoted_artifact_sha256"])
        try:
            _path, resolved_sha256 = self._exports.resolve_export(
                artifact_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            )
        except AdapterExportError as exc:
            raise UnslothMutationError(
                exc.reason_code,
                str(exc),
                status_code=404 if exc.reason_code == "export_not_found" else 409,
            ) from exc
        source_ids = tuple(operation_payload["source_ids"])
        run_ids = tuple(operation_payload["run_ids"])
        try:
            promotion, evidence = validate_runtime_promotion_binding(
                record=record,
                artifact_sha256=artifact_sha256,
                resolved_sha256=resolved_sha256,
                source_ids=source_ids,
                run_ids=run_ids,
            )
            descriptor = self._integrations.normalize_runtime_endpoint_descriptor(
                provider_descriptor=operation_payload["provider_descriptor"],
                endpoint_descriptor=operation_payload["endpoint_descriptor"],
            )
            request = RuntimeHandoffRequest(
                tenant_id=principal.tenant_id,
                endpoint_id=str(descriptor["endpoint"]["endpoint_id"]),
                provider=str(descriptor["provider"]["provider_id"]),
                artifact=RuntimeArtifact(
                    artifact_id=artifact_id,
                    tenant_id=principal.tenant_id,
                    artifact_sha256=artifact_sha256,
                    registry_state="promoted",
                    verification_state="verified",
                    format="adapter",
                ),
                source_ids=source_ids,
                run_ids=run_ids,
                expected_endpoint_revision=int(
                    operation_payload["expected_endpoint_revision"]
                ),
                provider_descriptor=descriptor["provider"],
                endpoint_descriptor=descriptor["endpoint"],
                api_capabilities=descriptor["api_capabilities"],
                limits=descriptor["limits"],
                promotion_id=str(promotion.get("promotion_id") or ""),
                adapter_id=record.adapter_id,
                adapter_sha256=str(record.artifact_sha256 or ""),
                base_model_id=record.base_model,
                base_model_sha256=str(
                    evidence.get("base_model_sha256") or ""
                ),
                job_id=str(evidence.get("job_id") or ""),
                attempt_id=str(evidence.get("attempt_id") or ""),
                fencing_token_digest=str(
                    evidence.get("fencing_token_digest") or ""
                ),
                reason_sha256=_sha256(reason),
            )
            return self._handoff.plan(request)
        except RuntimeHandoffError as exc:
            raise _mutation_error(exc) from exc
        except (TypeError, ValueError) as exc:
            reason_code = str(
                getattr(exc, "reason_code", "runtime_endpoint_descriptor_invalid")
            )
            raise UnslothMutationError(
                reason_code,
                str(exc),
                status_code=409,
            ) from exc


def build_runtime_handoff_mutation_executor(
    *,
    agent_config: Mapping[str, Any],
    export_service: MlInternAdapterExportService,
    adapter_registry: MlInternAdapterRegistryService,
    storage_references: StorageReferencePort | None = None,
) -> UnslothRuntimeHandoffMutationExecutor:
    training = dict(agent_config.get("ml_intern_training") or {})
    runtime = dict(agent_config.get("lora_runtime") or {})
    unsloth = dict(agent_config.get("unsloth") or training.get("unsloth") or {})
    trusted_source_ids = list(
        unsloth.get("trusted_source_ids")
        or training.get("trusted_source_ids")
        or []
    )
    trusted_run_ids = list(
        unsloth.get("trusted_run_ids")
        or training.get("trusted_run_ids")
        or []
    )
    registry_path = str(
        runtime.get("runtime_endpoint_registry_path")
        or Path(
            str(
                runtime.get("adapter_registry_path")
                or Path(
                    str(training.get("artifact_root") or "artifacts/lora")
                )
                / "adapter_registry.json"
            )
        ).with_name("runtime_endpoints.sqlite3")
    )
    endpoints = SqliteRuntimeEndpointRegistry(
        registry_path,
        storage_references=storage_references,
    )
    tasks = HubRuntimeHandoffTaskController(
        handler=RuntimeHandoffTaskHandler(endpoints)
    )
    handoff = UnslothRuntimeHandoffService(
        tasks=tasks,
        audit=_RuntimeHandoffAudit(),
        evidence=ProvidedEvidenceRegistry(
            source_ids=trusted_source_ids,
            run_ids=trusted_run_ids,
        ),
    )
    return UnslothRuntimeHandoffMutationExecutor(
        handoff=handoff,
        endpoints=endpoints,
        export_service=export_service,
        adapter_registry=adapter_registry,
        integrations=get_integration_registry_service(),
    )


def runtime_endpoint_registry_from_config(
    agent_config: Mapping[str, Any],
    *,
    storage_references: StorageReferencePort | None = None,
) -> SqliteRuntimeEndpointRegistry:
    training = dict(agent_config.get("ml_intern_training") or {})
    runtime = dict(agent_config.get("lora_runtime") or {})
    registry_path = str(
        runtime.get("runtime_endpoint_registry_path")
        or Path(
            str(
                runtime.get("adapter_registry_path")
                or Path(
                    str(training.get("artifact_root") or "artifacts/lora")
                )
                / "adapter_registry.json"
            )
        ).with_name("runtime_endpoints.sqlite3")
    )
    return SqliteRuntimeEndpointRegistry(
        registry_path,
        storage_references=storage_references,
    )


def _mutation_error(exc: Exception) -> UnslothMutationError:
    return UnslothMutationError(
        str(getattr(exc, "reason_code", getattr(exc, "code", "runtime_handoff_failed"))),
        str(exc),
        status_code=409,
        retryable=bool(getattr(exc, "retryable", False)),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "HubRuntimeHandoffTaskController",
    "RuntimeHandoffTaskHandler",
    "UnslothRuntimeHandoffMutationExecutor",
    "build_runtime_handoff_mutation_executor",
    "runtime_endpoint_registry_from_config",
]
