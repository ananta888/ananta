"""Hub-side composition helpers for ML-Intern Unsloth integrations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from flask import current_app

from agent.routes.ml_intern_training_route_support import (
    _environment_training_overrides,
    _route_audit_sink,
)
from agent.services.ml_intern_adapter_export_service import (
    MlInternAdapterExportService,
)
from agent.services.ml_intern_adapter_import_service import (
    MlInternAdapterImportService,
)
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
)
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_catalog_service import (
    MlInternDatasetCatalogService,
)
from agent.services.ml_intern_dataset_preview_service import (
    DatasetPreviewPolicy,
    MlInternDatasetPreviewService,
)
from agent.services.ml_intern_dataset_repository_bridge_service import (
    MlInternDatasetRepositoryBridgeService,
)
from agent.services.ml_intern_dataset_split_service import (
    MlInternDatasetSplitService,
)
from agent.services.ml_intern_evaluation_promotion_facade import (
    MlInternEvaluationPromotionFacade,
)
from agent.services.ml_intern_evaluation_store_service import (
    MlInternEvaluationStoreService,
)
from agent.services.ml_intern_training_config_service import (
    normalize_lora_runtime_config,
    normalize_ml_intern_training_config,
)
from agent.services.ml_intern_training_control_service import (
    MlInternTrainingControlService,
    get_ml_intern_training_control_service,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.ml_intern_training_repository_provider import (
    get_ml_intern_training_repository,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_mcp_adapter import (
    UnslothMcpAdapter,
    UnslothMcpError,
    default_unsloth_mcp_tool_policies,
)
from agent.services.unsloth_model_catalog_service import (
    SqliteUnslothModelCatalogRegistry,
    UnslothModelImportResultHandler,
    get_unsloth_model_catalog_registry,
)
from agent.services.unsloth_model_source_adapter import (
    ModelSourceRequest,
    ModelSourceValidationError,
    UnslothModelSourceAdapter,
)
from agent.services.unsloth_mutation_command_service import (
    AdapterExportMutationExecutor,
    SqliteUnslothMutationLedger,
    UnslothMutationCommandService,
    UnslothMutationError,
    UnslothMutationExecutor,
    UnslothOperationPayloadExecutor,
)
from agent.services.unsloth_runtime_handoff_composition import (
    build_runtime_handoff_mutation_executor,
)
from agent.services.unsloth_storage_governance_service import (
    SqliteUnslothStorageCatalog,
    UnslothStorageCleanupMutationExecutor,
    storage_catalog_from_config,
)
from agent.services.unsloth_studio_transport import (
    UnslothStudioTransport,
    UnslothStudioTransportConfig,
    UnslothStudioTransportError,
)
from agent.services.unsloth_studio_worker_adapter import (
    HubTaskSubmissionCommandAdapter,
    UnslothStudioWorkerAdapter,
)
from agent.services.unsloth_task_port import (
    CallableUnslothAuditAdapter,
    HubTaskSubmissionPort,
    HubUnslothTaskSubmissionAdapter,
)

_MULTIPART_OVERHEAD_BYTES = 512 * 1024


@dataclass(frozen=True)
class _TrainingServices:
    config: dict[str, Any]
    catalog: MlInternDatasetCatalogService
    preview: MlInternDatasetPreviewService
    split: MlInternDatasetSplitService
    bridge: MlInternDatasetRepositoryBridgeService
    adapter_import: MlInternAdapterImportService
    registry: MlInternAdapterRegistryService
    adapter_export: MlInternAdapterExportService
    evaluation_store: MlInternEvaluationStoreService
    control: MlInternTrainingControlService
    storage: SqliteUnslothStorageCatalog


def _services() -> _TrainingServices:
    raw_agent = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    raw_training = {
        **dict(raw_agent.get("ml_intern_training") or {}),
        **_environment_training_overrides(),
    }
    config = normalize_ml_intern_training_config(raw_training)
    dataset_root = Path(config["dataset_root"])
    artifact_root = Path(config["artifact_root"])
    dataset_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    storage = storage_catalog_from_config(config)
    catalog_root = Path(raw_training.get("dataset_catalog_root") or dataset_root / "catalog")
    policy = ArtifactSecurityPolicy(
        max_file_bytes=int(config["max_dataset_bytes"]),
        max_request_bytes=int(config["max_dataset_bytes"]) + _MULTIPART_OVERHEAD_BYTES,
        max_tenant_bytes=int(config["max_dataset_bytes"]) * 20,
        max_archive_uncompressed_bytes=int(config["max_dataset_bytes"]) * 2,
    )
    catalog = MlInternDatasetCatalogService(
        storage_root=catalog_root,
        security=MlInternArtifactSecurityService(storage_root=catalog_root, policy=policy),
        audit_sink=_route_audit_sink,
    )
    repository = get_ml_intern_training_repository()
    bridge = MlInternDatasetRepositoryBridgeService(
        execution_root=dataset_root,
        catalog=catalog,
        repository=repository,
        max_dataset_bytes=int(config["max_dataset_bytes"]),
        storage_catalog=storage,
    )
    import_root = artifact_root / "adapter-imports"
    import_policy = ArtifactSecurityPolicy(
        max_file_bytes=int(config["max_adapter_bytes"]),
        max_request_bytes=int(config["max_adapter_bytes"]) + _MULTIPART_OVERHEAD_BYTES,
        max_tenant_bytes=int(config["max_adapter_bytes"]) * 8,
        max_archive_uncompressed_bytes=int(config["max_adapter_bytes"]),
    )
    adapter_import = MlInternAdapterImportService(
        storage_root=import_root,
        security=MlInternArtifactSecurityService(storage_root=import_root, policy=import_policy),
    )
    raw_runtime = dict(raw_agent.get("lora_runtime") or {})
    raw_runtime.setdefault("adapter_registry_path", str(artifact_root / "adapter_registry.json"))
    runtime = normalize_lora_runtime_config(raw_runtime)
    registry = MlInternAdapterRegistryService(runtime["adapter_registry_path"])
    control_config = {
        **raw_training,
        "lora_runtime": raw_runtime,
    }
    return _TrainingServices(
        config=config,
        catalog=catalog,
        preview=MlInternDatasetPreviewService(
            catalog,
            policy=DatasetPreviewPolicy(max_page_size=int(config["max_preview_records"])),
        ),
        split=MlInternDatasetSplitService(catalog),
        bridge=bridge,
        adapter_import=adapter_import,
        registry=registry,
        adapter_export=MlInternAdapterExportService(
            artifact_root=artifact_root,
            registry=registry,
            storage_catalog=storage,
        ),
        evaluation_store=MlInternEvaluationStoreService(
            artifact_root=artifact_root,
            storage_references=storage,
        ),
        control=get_ml_intern_training_control_service(control_config),
        storage=storage,
    )


def _unsloth_promotion_facade(
    services: _TrainingServices,
) -> MlInternEvaluationPromotionFacade:
    security = dict(services.config.get("unsloth_security") or {})
    return MlInternEvaluationPromotionFacade(
        evaluations=services.evaluation_store,
        registry=services.registry,
        trusted_source_ids=tuple(security.get("trusted_source_ids") or ()),
        trusted_run_ids=tuple(security.get("trusted_run_ids") or ()),
        audit_sink=_route_audit_sink,
        storage_references=services.storage,
    )


def _unsloth_tasks() -> HubTaskSubmissionPort:
    configured = current_app.extensions.get("unsloth_task_submission_port")
    if configured is not None:
        return configured
    adapter = HubUnslothTaskSubmissionAdapter()
    current_app.extensions["unsloth_task_submission_port"] = adapter
    return adapter


def _unsloth_studio_transport(
    services: _TrainingServices,
) -> UnslothStudioTransport:
    configured = current_app.extensions.get("unsloth_studio_transport")
    if configured is not None:
        return configured
    security = dict(services.config.get("unsloth_security") or {})
    if not services.config.get("unsloth_integration_enabled"):
        raise RuntimeError("unsloth_studio_integration_disabled")
    try:
        transport = UnslothStudioTransport(
            config=UnslothStudioTransportConfig(
                base_url=str(security.get("studio_url") or ""),
                credential_secret_ref=str(security.get("auth_secret_ref") or ""),
                expected_studio_version=str(security.get("expected_studio_version") or ""),
                allowed_hosts=tuple(security.get("allowed_hosts") or ()),
                allowed_ip_cidrs=tuple(security.get("allowed_ip_cidrs") or ()),
                external_network_enabled=bool(
                    services.config.get(
                        "external_network_allowed",
                        False,
                    )
                ),
                local_network_enabled=bool(security.get("local_network_enabled")),
                allow_plaintext_internal=not bool(security.get("tls_required", True)),
            )
        )
    except ValueError as exc:
        raise UnslothStudioTransportError("unsloth_studio_configuration_invalid") from exc
    current_app.extensions["unsloth_studio_transport"] = transport
    return transport


def _unsloth_studio_adapter(
    services: _TrainingServices,
) -> UnslothStudioWorkerAdapter:
    configured = current_app.extensions.get("unsloth_studio_worker_adapter")
    if configured is not None:
        return configured
    adapter = UnslothStudioWorkerAdapter(
        transport=_unsloth_studio_transport(services),
        hub_task_commands=HubTaskSubmissionCommandAdapter(_unsloth_tasks()),
        allowed_mutations=("stop_training",),
    )
    current_app.extensions["unsloth_studio_worker_adapter"] = adapter
    return adapter


def _unsloth_mcp_adapter(
    services: _TrainingServices,
) -> UnslothMcpAdapter:
    configured = current_app.extensions.get("unsloth_mcp_adapter")
    if configured is not None:
        return configured
    security = dict(services.config.get("unsloth_security") or {})
    if not security.get("mcp_enabled"):
        raise RuntimeError("unsloth_mcp_disabled")
    replay_store = current_app.extensions.get("unsloth_mcp_replay_store")
    if replay_store is None:
        from agent.services.workflow_control_production_composition import (
            production_command_replay_store,
        )

        replay_store = production_command_replay_store()
        current_app.extensions["unsloth_mcp_replay_store"] = replay_store
    try:
        adapter = UnslothMcpAdapter(
            transport=_unsloth_studio_transport(services),
            studio_adapter=_unsloth_studio_adapter(services),
            replay_store=replay_store,
            mcp_bearer_secret_ref=str(security.get("mcp_auth_secret_ref") or ""),
            tool_policies=default_unsloth_mcp_tool_policies(),
            audit_sink=_route_audit_sink,
        )
    except ValueError as exc:
        raise UnslothMcpError("unsloth_mcp_configuration_invalid") from exc
    current_app.extensions["unsloth_mcp_adapter"] = adapter
    return adapter


def _unsloth_integration_probe(
    services: _TrainingServices,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "ananta.unsloth-integration-capabilities.v1",
        "studio": {
            "available": False,
            "reason_code": "unsloth_studio_integration_disabled",
        },
        "mcp": {
            "available": False,
            "reason_code": "unsloth_mcp_disabled",
        },
    }
    if not services.config.get("unsloth_integration_enabled"):
        return result
    try:
        result["studio"] = dict(_unsloth_studio_adapter(services).probe())
    except UnslothStudioTransportError as exc:
        result["studio"] = {
            "available": False,
            "reason_code": exc.reason_code,
        }
        result["mcp"] = {
            "available": False,
            "reason_code": "unsloth_studio_probe_failed",
        }
        return result
    security = dict(services.config.get("unsloth_security") or {})
    if not security.get("mcp_enabled"):
        return result
    try:
        result["mcp"] = dict(_unsloth_mcp_adapter(services).probe())
    except UnslothMcpError as exc:
        result["mcp"] = {
            "available": False,
            "reason_code": exc.reason_code,
        }
    return result


def _compose_unsloth_integration_facets(
    raw_capabilities: Any,
    integration: Mapping[str, Any],
) -> dict[str, Any]:
    capabilities = dict(raw_capabilities if isinstance(raw_capabilities, Mapping) else {})
    raw_facets = capabilities.get("facets")
    facets = [dict(facet) for facet in raw_facets if isinstance(facet, Mapping)] if isinstance(raw_facets, list) else []
    studio = dict(integration.get("studio") if isinstance(integration.get("studio"), Mapping) else {})
    mcp = dict(integration.get("mcp") if isinstance(integration.get("mcp"), Mapping) else {})
    facets = _replace_unsloth_facet(
        facets,
        facet_id="studio.management",
        available=studio.get("available") is True,
        unavailable_reason="unsloth_studio_client_unavailable",
        operations=("health", "status"),
    )
    facets = _replace_unsloth_facet(
        facets,
        facet_id="mcp.control",
        available=(studio.get("available") is True and mcp.get("available") is True),
        unavailable_reason="unsloth_mcp_client_unavailable",
        operations=tuple(
            str(tool_id)
            for tool_id in (mcp.get("tool_ids") if isinstance(mcp.get("tool_ids"), list) else ())
            if str(tool_id)
        ),
    )
    capabilities["facets"] = sorted(
        facets,
        key=lambda facet: str(facet.get("id") or ""),
    )
    snapshot_payload = dict(capabilities)
    snapshot_payload.pop("snapshot_id", None)
    capabilities["snapshot_id"] = hashlib.sha256(
        json.dumps(
            snapshot_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return capabilities


def _replace_unsloth_facet(
    facets: list[dict[str, Any]],
    *,
    facet_id: str,
    available: bool,
    unavailable_reason: str,
    operations: tuple[str, ...],
) -> list[dict[str, Any]]:
    replacement = {
        "id": facet_id,
        "available": available,
        "reason_code": (None if available else unavailable_reason),
        "source": "hub_policy",
        "operations": list(operations),
        "model_kinds": [],
    }
    return [
        *[facet for facet in facets if facet.get("id") != facet_id],
        replacement,
    ]


def _set_unsloth_facet_availability(
    current: Any,
    available: bool,
    reason_code: str | None,
) -> Any:
    if not isinstance(current, Mapping):
        return bool(available)
    facet = dict(current)
    for field in ("available", "enabled", "supported"):
        if field in facet:
            facet[field] = bool(available)
            break
    else:
        facet["available"] = bool(available)
    if "reason_code" in facet:
        facet["reason_code"] = None if available else reason_code
    return facet


def _unsloth_evidence(services: _TrainingServices) -> ProvidedEvidenceRegistry:
    security = dict(services.config.get("unsloth_security") or {})
    return ProvidedEvidenceRegistry(
        source_ids=tuple(security.get("trusted_source_ids") or ()),
        run_ids=tuple(security.get("trusted_run_ids") or ()),
    )


def _unsloth_model_source_adapter(
    services: _TrainingServices,
) -> UnslothModelSourceAdapter:
    return UnslothModelSourceAdapter(
        tasks=_unsloth_tasks(),
        audit=CallableUnslothAuditAdapter(_route_audit_sink),
        evidence=_unsloth_evidence(services),
    )


def _unsloth_model_registry(
    services: _TrainingServices,
) -> SqliteUnslothModelCatalogRegistry:
    return get_unsloth_model_catalog_registry(
        artifact_root=services.config["artifact_root"],
    )


def _unsloth_model_import_result_handler(
    services: _TrainingServices,
) -> UnslothModelImportResultHandler:
    return UnslothModelImportResultHandler(_unsloth_model_registry(services))


def _unsloth_model_source_request(
    services: _TrainingServices,
    principal: MlInternTrainingPrincipal,
    body: Mapping[str, Any],
) -> ModelSourceRequest:
    allowed = {
        "project_id",
        "source_id",
        "kind",
        "expected_sha256",
        "artifact_id",
        "model_id",
        "revision",
        "max_bytes",
        "allow_patterns",
        "trust_remote_code",
        "network_authorized",
        "license_status",
        "format",
        "architecture",
        "quantization",
        "capability_facets",
    }
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ModelSourceValidationError(
            "model_import_unknown_fields",
            f"Unknown model import fields: {', '.join(unknown[:10])}.",
        )
    allow_patterns = body.get("allow_patterns", [])
    facets = body.get("capability_facets", [])
    if not isinstance(allow_patterns, list) or not isinstance(facets, list):
        raise ModelSourceValidationError(
            "model_import_array_invalid",
            "allow_patterns and capability_facets must be arrays.",
        )
    trust_remote_code = body.get("trust_remote_code", False)
    network_authorized = body.get("network_authorized", False)
    if not isinstance(trust_remote_code, bool) or not isinstance(network_authorized, bool):
        raise ModelSourceValidationError(
            "model_import_boolean_invalid",
            "Network and remote-code decisions must be JSON booleans.",
        )
    if network_authorized and not bool(services.config.get("external_network_allowed", False)):
        raise ModelSourceValidationError(
            "model_import_network_policy_denied",
            "Hub network policy does not authorize remote model downloads.",
        )
    max_bytes = body.get("max_bytes", 20 * 1024**3)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ModelSourceValidationError("model_import_size_invalid", "max_bytes must be an integer.")
    return ModelSourceRequest(
        tenant_id=principal.tenant_id,
        project_id=str(body.get("project_id") or principal.subject),
        source_id=str(body.get("source_id") or ""),
        kind=str(body.get("kind") or ""),
        expected_sha256=str(body.get("expected_sha256") or ""),
        artifact_id=body.get("artifact_id"),
        model_id=body.get("model_id"),
        revision=body.get("revision"),
        max_bytes=max_bytes,
        allow_patterns=tuple(str(item) for item in allow_patterns),
        trust_remote_code=trust_remote_code,
        network_authorized=network_authorized,
        license_status=str(body.get("license_status") or "pending"),
        model_format=str(body.get("format") or "transformers"),
        architecture=str(body.get("architecture") or "unknown"),
        quantization=body.get("quantization"),
        capability_facets=tuple(str(item) for item in facets),
    )


def _unsloth_mutation_executors(
    services: _TrainingServices,
) -> dict[str, UnslothMutationExecutor]:
    configured = current_app.extensions.get("unsloth_export_mutation_executor")
    if configured is not None:
        if not isinstance(configured, UnslothMutationExecutor):
            return {}
        export_executor = configured
    else:
        export_executor = AdapterExportMutationExecutor(services.adapter_export)
    configured_runtime = current_app.extensions.get("unsloth_runtime_handoff_mutation_executor")
    if configured_runtime is not None:
        if not (
            isinstance(configured_runtime, UnslothMutationExecutor)
            or isinstance(configured_runtime, UnslothOperationPayloadExecutor)
        ):
            return {"export": export_executor}
        runtime_executor = configured_runtime
    else:
        runtime_executor = build_runtime_handoff_mutation_executor(
            agent_config=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
            export_service=services.adapter_export,
            adapter_registry=services.registry,
            storage_references=services.storage,
        )
    configured_cleanup = current_app.extensions.get("unsloth_cleanup_mutation_executor")
    if configured_cleanup is not None:
        if not (
            isinstance(configured_cleanup, UnslothMutationExecutor)
            or isinstance(configured_cleanup, UnslothOperationPayloadExecutor)
        ):
            return {
                "export": export_executor,
                "runtime_handoff": runtime_executor,
            }
        cleanup_executor = configured_cleanup
    else:
        cleanup_executor = UnslothStorageCleanupMutationExecutor(
            catalog=services.storage,
            tasks=_unsloth_tasks(),
        )
    # MCP remains absent until its separate tool contract is composed.
    return {
        "export": export_executor,
        "runtime_handoff": runtime_executor,
        "cleanup": cleanup_executor,
    }


def _unsloth_confirmation_secret() -> bytes | None:
    value = current_app.secret_key
    if isinstance(value, bytes):
        encoded = value
    else:
        encoded = str(value or "").encode("utf-8")
    return encoded if len(encoded) >= 32 else None


def _unsloth_mutation_service(
    services: _TrainingServices,
) -> UnslothMutationCommandService:
    secret = _unsloth_confirmation_secret()
    if secret is None:
        raise UnslothMutationError(
            "unsloth_confirmation_secret_unavailable",
            "A strong Hub confirmation secret is required.",
            status_code=503,
        )
    ledger_path = Path(services.config["artifact_root"]) / ".control" / "unsloth-mutation-idempotency.sqlite3"
    return UnslothMutationCommandService(
        executors=_unsloth_mutation_executors(services),
        ledger=SqliteUnslothMutationLedger(ledger_path),
        confirmation_secret=secret,
    )


def _audit_unsloth_mutation(
    principal: MlInternTrainingPrincipal,
    *,
    operation: str,
    resource_id: str | None,
    dry_run: bool | None,
    outcome: str,
    reason_code: str,
    replayed: bool = False,
) -> None:
    _route_audit_sink(
        "ml_intern_unsloth_mutation",
        {
            "tenant_id": principal.tenant_id,
            "actor": principal.subject,
            "operation": str(operation or "")[:64],
            "resource_id": resource_id,
            "dry_run": dry_run,
            "outcome": outcome,
            "reason_code": reason_code,
            "replayed": replayed,
        },
    )
