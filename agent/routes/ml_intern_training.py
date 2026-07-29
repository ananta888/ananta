from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, Response, current_app, g, request, send_file, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import MlInternDatasetDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepositoryConflict,
    get_ml_intern_training_repository,
)
from agent.services.ml_intern_adapter_export_service import (
    AdapterExportError,
    MlInternAdapterExportService,
)
from agent.services.ml_intern_adapter_import_service import (
    AdapterImportError,
    AdapterImportOutcome,
    MlInternAdapterImportService,
)
from agent.services.ml_intern_adapter_registry_service import (
    AdapterRecord,
    MlInternAdapterRegistryService,
    RegistryError,
    RegistryIdempotencyConflict,
    RegistryNotFoundError,
    RegistryVersionConflict,
)
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_catalog_service import (
    DatasetCatalogError,
    MlInternDatasetCatalogService,
)
from agent.services.ml_intern_dataset_preview_service import (
    DatasetPreviewError,
    DatasetPreviewPolicy,
    MlInternDatasetPreviewService,
)
from agent.services.ml_intern_dataset_repository_bridge_service import (
    MlInternDatasetRepositoryBridgeService,
)
from agent.services.ml_intern_dataset_split_service import (
    DatasetSplitError,
    MlInternDatasetSplitService,
)
from agent.services.ml_intern_evaluation_decision_service import evaluate_adapter_metrics
from agent.services.ml_intern_evaluation_store_service import (
    EvaluationStoreError,
    MlInternEvaluationStoreService,
)
from agent.services.ml_intern_training_config_service import (
    MlInternTrainingConfigError,
    normalize_lora_runtime_config,
    normalize_ml_intern_training_config,
)
from agent.services.ml_intern_training_contract import (
    BACKENDS,
    JOB_STATUSES,
    UNSLOTH_BACKENDS,
    MlInternTrainingContractError,
)
from agent.services.ml_intern_evaluation_promotion_facade import (
    MlInternEvaluationPromotionFacade,
    PromotionGateError,
)
from agent.services.ml_intern_training_control_service import (
    MlInternTrainingControlService,
    get_ml_intern_training_control_service,
)
from agent.services.ml_intern_training_job_service import get_training_job_service
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_mutation_command_service import (
    AdapterExportMutationExecutor,
    SqliteUnslothMutationLedger,
    UnslothMutationCommandService,
    UnslothMutationError,
    UnslothMutationExecutor,
    UnslothOperationPayloadExecutor,
    project_unsloth_capabilities,
)
from agent.services.unsloth_storage_governance_service import (
    SqliteUnslothStorageCatalog,
    UnslothStorageCleanupMutationExecutor,
    UnslothStorageError,
    storage_catalog_from_config,
    tenant_scope_digest,
)
from agent.services.unsloth_runtime_handoff_composition import (
    build_runtime_handoff_mutation_executor,
)
from agent.services.unsloth_data_recipe_adapter import (
    DataRecipeRequest,
    DataRecipeValidationError,
    UnslothDataRecipeAdapter,
)
from agent.services.unsloth_data_recipe_composition_service import (
    RepositoryDatasetSnapshotAdapter,
    UnslothDataRecipeSubmissionService,
)
from agent.services.unsloth_completion_outbox_service import (
    get_unsloth_completion_outbox_reconciler,
)
from agent.services.unsloth_evidence import EvidenceVerificationError, ProvidedEvidenceRegistry
from agent.services.unsloth_model_catalog_service import (
    SqliteUnslothModelCatalogRegistry,
    UnslothModelCatalogError,
    UnslothModelImportResultHandler,
    get_unsloth_model_catalog_registry,
)
from agent.services.unsloth_model_source_adapter import (
    ModelSourceRequest,
    ModelSourceValidationError,
    UnslothModelSourceAdapter,
)
from agent.services.unsloth_task_port import (
    CallableUnslothAuditAdapter,
    HubTaskSubmissionPort,
    HubUnslothTaskSubmissionAdapter,
)
from agent.services.unsloth_mcp_adapter import (
    UnslothMcpAdapter,
    UnslothMcpError,
    default_unsloth_mcp_tool_policies,
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
from agent.services.workflow_runtime.security import InMemoryReplayNonceStore
from ananta_contracts.model_catalog import ModelCatalog

ml_intern_training_bp = Blueprint("ml_intern_training", __name__, url_prefix="/api/ml-intern-training")
_MULTIPART_OVERHEAD_BYTES = 512 * 1024
_ROUTE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


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


@ml_intern_training_bp.before_request
def _bound_training_request() -> Any:
    for name, value in dict(request.view_args or {}).items():
        if not _ROUTE_IDENTIFIER.fullmatch(str(value or "")):
            return _error(f"{name}_invalid", f"{name} is invalid", 422)
    if request.mimetype != "multipart/form-data":
        return
    config = _normalized_config()
    maximum = max(int(config["max_dataset_bytes"]), int(config["max_adapter_bytes"]))
    request.max_content_length = maximum + _MULTIPART_OVERHEAD_BYTES
    request.max_form_memory_size = 512 * 1024
    request.max_form_parts = 24


@ml_intern_training_bp.errorhandler(RequestEntityTooLarge)
def _request_too_large(_exc: RequestEntityTooLarge):
    return _error("upload_too_large", "training upload exceeds the configured byte limit", 413)


@ml_intern_training_bp.errorhandler(UnslothStorageError)
def _storage_governance_error(exc: UnslothStorageError):
    return _error(
        str(
            getattr(
                exc,
                "reason_code",
                getattr(exc, "code", "unsloth_storage_rejected"),
            )
        ),
        str(exc),
        int(getattr(exc, "status_code", 409)),
    )


@ml_intern_training_bp.errorhandler(MlInternTrainingContractError)
def _contract_error(exc: MlInternTrainingContractError):
    return _domain_error(exc)


@ml_intern_training_bp.route("/capabilities", methods=["GET"])
@check_auth
@admin_required
def capabilities():
    try:
        services = _services()
        result = services.control.capabilities()
        integration = _unsloth_integration_probe(services)
        result["unsloth_integration"] = integration
        result["unsloth_capabilities"] = _compose_unsloth_integration_facets(
            result.get("unsloth_capabilities"),
            integration,
        )
        result["unsloth"] = project_unsloth_capabilities(
            result.get("unsloth_capabilities"),
            executable_operations=(
                tuple(_unsloth_mutation_executors(services))
                if _unsloth_confirmation_secret() is not None
                else ()
            ),
        )
        return api_response(data=result)
    except (RuntimeError, ValueError) as exc:
        return _error("training_runtime_configuration_invalid", str(exc), 503, retryable=True)


@ml_intern_training_bp.route(
    "/unsloth/integration/capabilities",
    methods=["GET"],
)
@check_auth
@admin_required
def unsloth_integration_capabilities():
    try:
        return api_response(data=_unsloth_integration_probe(_services()))
    except (RuntimeError, ValueError) as exc:
        return _error(
            "training_runtime_configuration_invalid",
            str(exc),
            503,
            retryable=True,
        )


@ml_intern_training_bp.route(
    "/unsloth/mcp/tools/<tool_id>",
    methods=["POST"],
)
@check_auth
@admin_required
def execute_unsloth_mcp_tool(tool_id: str):
    try:
        body = _json_body()
        unknown = sorted(
            set(body)
            - {
                "arguments",
                "replay_nonce",
                "replay_expires_at",
                "confirmation_id",
                "correlation_id",
            }
        )
        if unknown:
            return _error(
                "unsloth_mcp_request_unknown_fields",
                "MCP request contains unsupported fields",
                422,
            )
        arguments = body.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return _error(
                "unsloth_mcp_arguments_invalid",
                "MCP arguments must be an object",
                422,
            )
        principal = _principal()
        result = _unsloth_mcp_adapter(_services()).execute(
            tool_id=tool_id,
            arguments=arguments,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            roles=("admin",),
            replay_nonce=str(body.get("replay_nonce") or ""),
            replay_expires_at=float(body.get("replay_expires_at") or 0),
            correlation_id=str(body.get("correlation_id") or ""),
            confirmation_id=(
                str(body.get("confirmation_id") or "").strip() or None
            ),
            idempotency_key=(
                str(request.headers.get("Idempotency-Key") or "").strip()
                or None
            ),
        )
        return api_response(
            data=result,
            code=202 if result.get("status") == "queued" else 200,
        )
    except MlInternTrainingConfigError as exc:
        return _error(
            exc.reason_code,
            str(exc),
            503,
            retryable=True,
        )
    except (TypeError, ValueError):
        return _error(
            "unsloth_mcp_request_invalid",
            "MCP request fields are invalid",
            422,
        )
    except UnslothStudioTransportError as exc:
        return (
            jsonify(
                _failure_envelope(
                    request_id=request_id,
                    reason_code=getattr(
                        exc,
                        "reason_code",
                        "unsloth_studio_upstream_unavailable",
                    ),
                    retryable=True,
                )
            ),
            503,
        )
    except UnslothMcpError as exc:
        status = 503 if exc.reason_code in {
            "incompatible_upstream_contract",
            "unsloth_mcp_probe_unavailable",
            "unsloth_mcp_upstream_unavailable",
            "unsloth_mcp_bearer_secret_unavailable",
            "unsloth_mcp_configuration_invalid",
        } else 409
        return _error(
            exc.reason_code,
            "MCP request was rejected by the Hub policy boundary",
            status,
            retryable=status == 503,
        )


@ml_intern_training_bp.route(
    "/unsloth/mutations/<operation>",
    methods=["POST"],
)
@check_auth
@admin_required
def apply_unsloth_mutation(operation: str):
    principal = _principal()
    resource_id: str | None = None
    dry_run: bool | None = None
    try:
        idempotency_key = _idempotency_key()
        body = _json_body()
        resource_id = (
            str(body.get("resource_id") or "").strip()[:128] or None
        )
        dry_run = body.get("dry_run") if isinstance(body.get("dry_run"), bool) else None
        result = _unsloth_mutation_service(_services()).execute(
            principal,
            route_operation=operation,
            payload=body,
            idempotency_key=idempotency_key,
        )
        _audit_unsloth_mutation(
            principal,
            operation=operation,
            resource_id=resource_id,
            dry_run=bool(result["dry_run"]),
            outcome="accepted",
            reason_code=str(result["reason_code"]),
            replayed=bool(result.get("replayed", False)),
        )
        code = (
            200
            if bool(result["dry_run"]) or bool(result.get("replayed", False))
            else 201
        )
        return api_response(data=result, code=code)
    except MlInternTrainingContractError as exc:
        _audit_unsloth_mutation(
            principal,
            operation=operation,
            resource_id=resource_id,
            dry_run=dry_run,
            outcome="denied",
            reason_code=exc.reason_code,
        )
        return _domain_error(exc)
    except UnslothStorageError as exc:
        _audit_unsloth_mutation(
            principal,
            operation=operation,
            resource_id=resource_id,
            dry_run=dry_run,
            outcome="denied",
            reason_code=str(
                getattr(
                    exc,
                    "reason_code",
                    "unsloth_storage_rejected",
                )
            ),
        )
        return _error(
            str(
                getattr(
                    exc,
                    "reason_code",
                    getattr(exc, "code", "unsloth_storage_rejected"),
                )
            ),
            str(exc),
            int(getattr(exc, "status_code", 409)),
        )
    except UnslothMutationError as exc:
        _audit_unsloth_mutation(
            principal,
            operation=operation,
            resource_id=resource_id,
            dry_run=dry_run,
            outcome="denied",
            reason_code=exc.reason_code,
        )
        return _error(
            exc.reason_code,
            str(exc),
            exc.status_code,
            retryable=exc.retryable,
        )


@ml_intern_training_bp.route("/unsloth/model-imports/plan", methods=["POST"])
@check_auth
@admin_required
def plan_unsloth_model_import():
    try:
        services = _services()
        plan = _unsloth_model_source_adapter(services).plan(
            _unsloth_model_source_request(services, _principal(), _json_body())
        )
        return api_response(
            data={
                "task_type": plan.task_type,
                "confirmation_digest": plan.confirmation_digest,
                "payload": json.loads(plan.payload_json),
            }
        )
    except (ModelSourceValidationError, EvidenceVerificationError) as exc:
        return _error(getattr(exc, "code", "model_import_invalid"), str(exc), 422)


@ml_intern_training_bp.route("/unsloth/model-imports", methods=["POST"])
@check_auth
@admin_required
def submit_unsloth_model_import():
    try:
        _idempotency_key()
        services = _services()
        body = _json_body()
        confirmation_digest = str(body.pop("confirmation_digest", "") or "")
        adapter = _unsloth_model_source_adapter(services)
        plan = adapter.plan(_unsloth_model_source_request(services, _principal(), body))
        task_id = adapter.submit(plan, confirmation_digest=confirmation_digest)
        return api_response(data={"task_id": task_id, "status": "queued"}, code=202)
    except (ModelSourceValidationError, EvidenceVerificationError) as exc:
        return _error(getattr(exc, "code", "model_import_invalid"), str(exc), 422)
    except ValueError as exc:
        return _error(str(exc), "model import task admission failed", 409)


@ml_intern_training_bp.route("/unsloth/model-imports/<task_id>/result", methods=["POST"])
@check_auth
@admin_required
def complete_unsloth_model_import(task_id: str):
    return _error(
        "model_import_direct_completion_gone",
        (
            "Model imports complete only through the validated "
            "Hub-to-Worker task result path."
        ),
        410,
    )


@ml_intern_training_bp.route("/unsloth/models", methods=["GET"])
@check_auth
@admin_required
def list_unsloth_imported_models():
    get_unsloth_completion_outbox_reconciler(
    ).reconcile_pending(limit=100)
    records = _unsloth_model_registry(_services()).list_versions(
        tenant_id=_principal().tenant_id
    )
    revision = max((record.catalog_revision for record in records), default=None)
    return api_response(
        data=ModelCatalog(
            catalog_revision=revision,
            imported_models=records,
        ).to_wire()
    )


@ml_intern_training_bp.route("/unsloth/data-recipes", methods=["POST"])
@check_auth
@admin_required
def submit_unsloth_data_recipe():
    try:
        _idempotency_key()
        services = _services()
        principal = _principal()
        body = _json_body()
        allowed = {
            "dataset_id",
            "source_id",
            "run_id",
            "objective",
            "prompt_field",
            "response_field",
            "validation_fraction",
            "seed",
            "media_field",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise DataRecipeValidationError(
                "data_recipe_unknown_fields",
                f"Unknown data recipe fields: {', '.join(unknown[:10])}.",
            )
        evidence = _unsloth_evidence(services)
        adapter = UnslothDataRecipeAdapter(
            datasets=RepositoryDatasetSnapshotAdapter(
                repository=get_ml_intern_training_repository(),
                principal=principal,
                dataset_root=services.config["dataset_root"],
            ),
            evidence=evidence,
        )
        submission = UnslothDataRecipeSubmissionService(
            adapter=adapter,
            tasks=_unsloth_tasks(),
        ).submit(
            DataRecipeRequest(
                tenant_id=principal.tenant_id,
                dataset_id=str(body.get("dataset_id") or ""),
                source_id=str(body.get("source_id") or ""),
                run_id=str(body.get("run_id") or ""),
                objective=str(body.get("objective") or ""),
                prompt_field=str(body.get("prompt_field") or ""),
                response_field=str(body.get("response_field") or ""),
                validation_fraction=body.get("validation_fraction", 0.05),
                seed=body.get("seed", 3407),
                media_field=body.get("media_field"),
            )
        )
        return api_response(
            data={
                "task_id": submission.task_id,
                "manifest": json.loads(submission.manifest.canonical_json()),
            },
            code=202,
        )
    except (DataRecipeValidationError, EvidenceVerificationError) as exc:
        return _error(getattr(exc, "code", "data_recipe_invalid"), str(exc), 422)
    except ValueError as exc:
        return _error(str(exc), "data recipe task admission failed", 409)


@ml_intern_training_bp.route("/datasets", methods=["GET"])
@check_auth
@admin_required
def list_datasets():
    principal = _principal()
    repository = get_ml_intern_training_repository()
    limit = _bounded_query_int("limit", 50, minimum=1, maximum=200)
    offset = _cursor_offset(request.args.get("cursor"))
    status = str(request.args.get("status") or "").strip()[:64]
    format_type = str(request.args.get("format") or "").strip()[:32]
    query = str(request.args.get("q") or "").strip().casefold()
    if len(query) > 160:
        raise MlInternTrainingContractError("query_too_long", "q must contain at most 160 characters")
    page: list[dict[str, Any]] = []
    total = 0
    scan_offset = 0
    while True:
        rows = repository.list_datasets(principal, limit=200, offset=scan_offset)
        for dataset in rows:
            row = MlInternTrainingReadModelService.dataset(dataset)
            if status and row["status"] != status and row["validation_status"] != status:
                continue
            if format_type and row["format"] != format_type:
                continue
            if query and query not in str(row.get("name") or "").casefold():
                continue
            if offset <= total < offset + limit:
                page.append(row)
            total += 1
        if len(rows) < 200:
            break
        scan_offset += len(rows)
    next_cursor = str(offset + len(page)) if offset + len(page) < total else None
    return api_response(data={"items": page, "count": len(page), "total": total, "next_cursor": next_cursor})


@ml_intern_training_bp.route("/datasets", methods=["POST"])
@check_auth
@admin_required
def upload_dataset():
    try:
        services = _services()
        principal = _principal()
        idempotency_key = _idempotency_key()
        if request.is_json:
            body = _json_body()
            records = body.get("records")
            if not isinstance(records, list):
                return _error("dataset_records_required", "records must be a JSON array", 400)
            summary = services.catalog.create_from_records(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                records=records,
                name=str(body.get("name") or "Curated dataset")[:160],
                dataset_format=str(body.get("format") or "instruction"),
                idempotency_key=idempotency_key,
            )
            metadata = {
                "purpose": body.get("purpose") or "",
                "license": body.get("license") or "",
                "license_status": body.get("license_status") or "pending",
                "privacy": body.get("privacy") or "private",
            }
            ratio = _bounded_body_float(
                body.get("validation_ratio"),
                default=float(services.config["validation_ratio"]),
                minimum=0.05,
                maximum=0.5,
            )
            seed = _bounded_body_int(
                body.get("split_seed"),
                default=int(services.config["split_seed"]),
                minimum=0,
                maximum=2**31 - 1,
            )
        else:
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                return _error("dataset_file_required", "multipart field 'file' is required", 400)
            summary = services.catalog.create_from_upload(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                stream=uploaded.stream,
                filename=uploaded.filename,
                media_type=uploaded.mimetype or "application/octet-stream",
                name=str(request.form.get("name") or "").strip() or None,
                dataset_format=str(request.form.get("format") or "instruction"),
                idempotency_key=idempotency_key,
                declared_size=uploaded.content_length or None,
                expected_sha256=str(request.form.get("sha256") or "").strip() or None,
            )
            metadata = {
                "purpose": request.form.get("purpose") or "",
                "license": request.form.get("license") or "",
                "license_status": request.form.get("license_status") or "pending",
                "privacy": request.form.get("privacy") or "private",
            }
            ratio = _form_float("validation_ratio", services.config["validation_ratio"])
            seed = _form_int("split_seed", services.config["split_seed"])
        projected = services.bridge.sync(principal, summary, metadata=metadata)
        split_result = services.split.split(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=str(summary["dataset_id"]),
            validation_ratio=ratio,
            seed=seed,
        )
        split_summary = split_result["dataset"]
        report = services.catalog.validate_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=str(summary["dataset_id"]),
        )
        projected = services.bridge.sync(
            principal,
            split_summary,
            validation_report=report,
            metadata=metadata,
        )
        log_audit(
            "ml_intern_dataset_ingested",
            {"dataset_id": projected["id"], "actor": principal.subject, "status": projected["status"]},
        )
        return api_response(data=_dataset_detail(projected, report), code=201)
    except (
        DatasetCatalogError,
        DatasetSplitError,
        DatasetPreviewError,
        MlInternTrainingContractError,
    ) as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>", methods=["GET"])
@check_auth
@admin_required
def get_dataset(dataset_id: str):
    principal = _principal()
    dataset = get_ml_intern_training_repository().get_dataset(principal, dataset_id)
    if dataset is None:
        return _error("dataset_not_found", "dataset does not exist", 404)
    projected = MlInternTrainingReadModelService.dataset(dataset)
    return api_response(data=_dataset_detail(projected, dataset.validation_report or {}))


@ml_intern_training_bp.route("/datasets/<dataset_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_dataset(dataset_id: str):
    try:
        _idempotency_key()
        services = _services()
        principal = _principal()
        repository = get_ml_intern_training_repository()
        dataset = repository.get_dataset(principal, dataset_id)
        if dataset is None:
            return _error("dataset_not_found", "dataset does not exist", 404)
        catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        services.catalog.assert_deletable(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
        )
        dataset_snapshot = MlInternDatasetDB.model_validate(dataset.model_dump())
        if not repository.delete_dataset(principal, dataset_id):
            return _error("dataset_not_found", "dataset does not exist", 404)
        try:
            services.catalog.delete_dataset(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                dataset_id=catalog_id,
            )
        except Exception:
            # The SQL projection is restored exactly when filesystem catalog
            # deletion did not commit. Referenced datasets are rejected before
            # this point by the repository transaction.
            repository.create_dataset(dataset_snapshot)
            raise
        log_audit(
            "ml_intern_dataset_deleted",
            {"dataset_id": dataset_id, "actor": principal.subject},
        )
        return api_response(data={"id": dataset_id, "deleted": True})
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except MlInternTrainingRepositoryConflict as exc:
        return _error(str(exc), "referenced dataset cannot be deleted", 409)
    except (DatasetCatalogError, MlInternTrainingContractError) as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>/records", methods=["GET"])
@check_auth
@admin_required
def preview_dataset(dataset_id: str):
    try:
        services = _services()
        principal = _principal()
        catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        partition = str(request.args.get("split") or "train")
        result = services.preview.get_page(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
            partition=partition,
            cursor=str(request.args.get("cursor") or "") or None,
            limit=_bounded_query_int("limit", 25, minimum=1, maximum=100),
        )
        items = []
        for row in result["records"]:
            record = dict(row.get("record") or {})
            items.append(
                {
                    "index": int(row.get("record_index") or 0),
                    "split": partition,
                    "valid": row.get("state") == "ready",
                    "reason_codes": [] if row.get("state") == "ready" else [row.get("state")],
                    **record,
                }
            )
        return api_response(
            data={
                "items": items,
                "count": len(items),
                "next_cursor": result.get("next_cursor"),
                "state": result.get("state"),
            }
        )
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except DatasetPreviewError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>/statistics", methods=["GET"])
@check_auth
@admin_required
def dataset_statistics(dataset_id: str):
    try:
        services = _services()
        principal = _principal()
        catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        result = services.preview.get_statistics(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
        )
        result["dataset_id"] = dataset_id
        return api_response(data=result)
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except DatasetPreviewError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>/split", methods=["POST"])
@check_auth
@admin_required
def split_dataset(dataset_id: str):
    try:
        _idempotency_key()
        body = _json_body()
        services = _services()
        principal = _principal()
        catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        result = services.split.split(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
            validation_ratio=body.get("validation_ratio", services.config["validation_ratio"]),
            seed=body.get("seed", services.config["split_seed"]),
        )
        projected = services.bridge.sync(principal, result["dataset"])
        return api_response(data=_dataset_detail(projected, {}))
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except (DatasetSplitError, DatasetCatalogError, MlInternTrainingContractError) as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>/validate", methods=["POST"])
@check_auth
@admin_required
def validate_dataset(dataset_id: str):
    try:
        _idempotency_key()
        body = _json_body()
        unknown = sorted(set(body) - {"allow_sensitive_override", "override_reason"})
        if unknown:
            raise MlInternTrainingContractError(
                "dataset_validation_unknown_fields",
                f"unknown validation fields: {', '.join(unknown[:10])}",
            )
        supplied_override = body.get("allow_sensitive_override", False)
        if not isinstance(supplied_override, bool):
            raise MlInternTrainingContractError(
                "allow_sensitive_override_invalid",
                "allow_sensitive_override must be a JSON boolean",
            )
        override = supplied_override is True
        if body.get("override_reason") is not None and not override:
            raise MlInternTrainingContractError(
                "override_reason_without_override",
                "override_reason requires allow_sensitive_override=true",
            )
        services = _services()
        principal = _principal()
        catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        report = services.catalog.validate_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
            allow_sensitive_override=override,
            is_admin=bool(getattr(g, "is_admin", False)),
            override_reason=body.get("override_reason"),
        )
        summary = services.catalog.get_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
        )
        services.bridge.sync(principal, summary, validation_report=report)
        return api_response(data=_validation_read_model(dataset_id, report))
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except (DatasetCatalogError, MlInternTrainingContractError) as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/datasets/<dataset_id>/validation-report", methods=["GET"])
@check_auth
@admin_required
def get_dataset_validation_report(dataset_id: str):
    principal = _principal()
    dataset = get_ml_intern_training_repository().get_dataset(principal, dataset_id)
    if dataset is None:
        return _error("dataset_not_found", "dataset does not exist", 404)
    report = dataset.validation_report if isinstance(dataset.validation_report, Mapping) else {}
    if not report:
        return _error("validation_report_not_found", "dataset has no validation report", 404)
    return api_response(data=_validation_read_model(dataset_id, report))


@ml_intern_training_bp.route("/datasets/<dataset_id>/validation-dataset", methods=["POST"])
@check_auth
@admin_required
def attach_validation_dataset(dataset_id: str):
    """Bind a separately uploaded dataset as the immutable validation split."""

    try:
        _idempotency_key()
        body = _json_body()
        validation_dataset_id = str(body.get("validation_dataset_id") or "").strip()
        if not validation_dataset_id:
            return _error(
                "validation_dataset_id_required",
                "validation_dataset_id is required",
                400,
            )
        services = _services()
        principal = _principal()
        train_catalog_id = services.bridge.catalog_dataset_id(principal, dataset_id)
        validation_catalog_id = services.bridge.catalog_dataset_id(principal, validation_dataset_id)
        result = services.split.attach_external_validation(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            train_dataset_id=train_catalog_id,
            validation_dataset_id=validation_catalog_id,
        )
        report = services.catalog.validate_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=train_catalog_id,
        )
        projected = services.bridge.sync(
            principal,
            result["dataset"],
            validation_report=report,
        )
        services.catalog.mark_referenced(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=validation_catalog_id,
            reference_id=train_catalog_id,
        )
        log_audit(
            "ml_intern_external_validation_attached",
            {
                "dataset_id": dataset_id,
                "validation_dataset_id": validation_dataset_id,
                "actor": principal.subject,
            },
        )
        response = _dataset_detail(projected, report)
        response["external_validation"] = {
            "dataset_id": validation_dataset_id,
            "semantic_overlap_count": result["pair"]["semantic_overlap_count"],
            "algorithm_version": result["manifest"]["algorithm_version"],
        }
        return api_response(data=response)
    except KeyError:
        return _error("dataset_not_found", "dataset does not exist", 404)
    except (DatasetSplitError, DatasetCatalogError, MlInternTrainingContractError) as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/jobs", methods=["POST"])
@check_auth
@admin_required
def submit_training_job():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("invalid_json", "JSON object body is required", 400)
    if "dataset_id" not in payload and "dataset_path" in payload:
        return _submit_legacy_job(payload)
    try:
        services = _services()
        principal = _principal()
        accepted, replayed = services.control.create_job(
            principal,
            payload,
            idempotency_key=_idempotency_key(),
        )
        if not replayed:
            try:
                catalog_id = services.bridge.catalog_dataset_id(principal, str(payload.get("dataset_id") or ""))
                services.catalog.mark_referenced(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.subject,
                    dataset_id=catalog_id,
                    reference_id=str(accepted["id"]),
                )
            except (DatasetCatalogError, KeyError):
                pass
            risk_reason = str(payload.get("risk_reason") or "").strip()
            log_audit(
                "ml_intern_training_job_admitted",
                {
                    "job_id": accepted["id"],
                    "dataset_id": payload.get("dataset_id"),
                    "mode": payload.get("mode", "dry_run"),
                    "backend": payload.get("backend", "mock"),
                    "actor": principal.subject,
                    "live_confirmed": payload.get("live_confirmed") is True,
                    "risk_reason_sha256": hashlib.sha256(risk_reason.encode()).hexdigest()
                    if risk_reason
                    else None,
                },
            )
        return api_response(data=accepted, code=200 if replayed else 202)
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/jobs", methods=["GET"])
@check_auth
@admin_required
def list_training_jobs():
    status = str(request.args.get("status") or "").strip() or None
    if status is not None and status not in JOB_STATUSES:
        return _error("job_status_invalid", "job status filter is invalid", 422)
    backend = str(request.args.get("backend") or "").strip().lower() or None
    if backend is not None and backend not in BACKENDS:
        return _error("job_backend_invalid", "job backend filter is invalid", 422)
    dataset_id = str(request.args.get("dataset_id") or "").strip() or None
    if dataset_id is not None and not _ROUTE_IDENTIFIER.fullmatch(dataset_id):
        return _error("dataset_id_invalid", "dataset filter is invalid", 422)
    result = _services().control.list_jobs(
        _principal(),
        limit=_bounded_query_int("limit", 50, minimum=1, maximum=200),
        offset=_cursor_offset(request.args.get("cursor")),
        status=status,
        backend=backend,
        dataset_id=dataset_id,
    )
    result["next_cursor"] = str(result.pop("next_offset")) if result.get("next_offset") is not None else None
    return api_response(data=result)


@ml_intern_training_bp.route("/jobs/<job_id>", methods=["GET"])
@check_auth
@admin_required
def get_training_job(job_id: str):
    try:
        return api_response(data=_services().control.get_job(_principal(), job_id))
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/jobs/<job_id>/events", methods=["GET"])
@check_auth
@admin_required
def list_training_job_events(job_id: str):
    try:
        if str(request.args.get("stream") or "").casefold() in {"1", "true", "yes"}:
            principal = _principal()
            control = _services().control
            control.get_job(principal, job_id)
            start_cursor = _bounded_query_int(
                "after_sequence",
                0,
                minimum=0,
                maximum=2**63 - 1,
            )

            @stream_with_context
            def generate():
                cursor = start_cursor
                deadline = time.monotonic() + 30 * 60
                while time.monotonic() < deadline:
                    page = control.list_events(
                        principal,
                        job_id,
                        after_sequence=cursor,
                        limit=100,
                    )
                    items = page["items"]
                    if items:
                        for item in items:
                            cursor = max(cursor, int(item["sequence"]))
                            yield f"id: {cursor}\ndata: {json.dumps(item, separators=(',', ':'), allow_nan=False)}\n\n"
                    else:
                        yield ": heartbeat\n\n"
                    detail = control.get_job(principal, job_id)
                    if detail["status"] in {"cancelled", "completed", "failed"} and not items:
                        break
                    time.sleep(1.0)

            response = Response(generate(), mimetype="text/event-stream")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Accel-Buffering"] = "no"
            return response
        result = _services().control.list_events(
            _principal(),
            job_id,
            after_sequence=_bounded_query_int("after_sequence", 0, minimum=0, maximum=2**63 - 1),
            limit=_bounded_query_int("limit", 200, minimum=1, maximum=500),
        )
        return api_response(data=result)
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@check_auth
@admin_required
def cancel_training_job(job_id: str):
    try:
        key = _idempotency_key()
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping) or set(payload) - {"reason"}:
            return _error("cancel_payload_invalid", "cancel body may only contain reason", 422)
        reason = str(payload.get("reason") or "").strip() or None
        if reason is not None and not 10 <= len(reason) <= 512:
            return _error("cancel_reason_invalid", "cancel reason must contain 10..512 characters", 422)
        principal = _principal()
        result = _services().control.cancel_job(
            principal,
            job_id,
            idempotency_key=key,
            reason=reason,
        )
        log_audit(
            "ml_intern_training_cancel_requested",
            {
                "job_id": job_id,
                "actor": principal.subject,
                "reason_code": "operator_cancel_requested",
                "reason_sha256": hashlib.sha256(reason.encode()).hexdigest() if reason else None,
            },
        )
        return api_response(data=result, code=202)
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/adapters", methods=["GET"])
@check_auth
@admin_required
def list_adapters():
    services = _services()
    principal = _principal()
    rows = [
        _adapter_read_model(row)
        for row in services.registry.list_adapters(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
    ]
    imports = services.adapter_import.list_imports(
        tenant_id=principal.tenant_id,
        principal_id=principal.subject,
    )
    existing = {
        (row["id"], str(row.get("adapter_version") or row["version"]))
        for row in rows
    }
    for item in imports:
        key = (str(item["adapter_id"]), str(item["version"]))
        if key not in existing:
            rows.append(_adapter_import_read_model(item))
    limit = _bounded_query_int("limit", 100, minimum=1, maximum=200)
    return api_response(data={"items": rows[:limit], "count": min(len(rows), limit), "total": len(rows)})


@ml_intern_training_bp.route("/adapters/import", methods=["POST"])
@check_auth
@admin_required
def import_adapter():
    try:
        services = _services()
        principal = _principal()
        adapter_id = str(request.form.get("adapter_id") or request.form.get("name") or "").strip()
        version = str(request.form.get("version") or "1").strip()
        base_model = str(request.form.get("base_model_id") or request.form.get("base_model") or "").strip()
        bundle = request.files.get("bundle")
        if bundle is not None and bundle.filename:
            outcome = services.adapter_import.import_archive_with_receipt(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                stream=bundle.stream,
                filename=bundle.filename,
                media_type=bundle.mimetype or "application/octet-stream",
                adapter_id=adapter_id,
                version=version,
                expected_base_model=base_model,
                idempotency_key=_idempotency_key(),
                declared_size=bundle.content_length or None,
            )
        else:
            config_file = request.files.get("adapter_config")
            weights_file = request.files.get("adapter_model")
            if config_file is None or weights_file is None:
                return _error(
                    "adapter_files_required",
                    "provide bundle or adapter_config plus adapter_model",
                    400,
                )
            outcome = services.adapter_import.import_files_with_receipt(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                adapter_config_stream=config_file.stream,
                adapter_weights_stream=weights_file.stream,
                adapter_id=adapter_id,
                version=version,
                expected_base_model=base_model,
                idempotency_key=_idempotency_key(),
                adapter_config_size=config_file.content_length or None,
                adapter_weights_size=weights_file.content_length or None,
            )
        result = outcome.summary
        imported_adapter_id = str(result.get("adapter_id") or adapter_id)
        try:
            record = _publish_imported_adapter(
                services,
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                result=result,
                display_name=str(request.form.get("name") or imported_adapter_id),
                method=str(request.form.get("method") or "lora"),
            )
        except Exception as exc:
            return _adapter_import_publication_error(services, outcome, exc)
        if outcome.compensation_token is not None:
            try:
                services.adapter_import.commit_import(outcome.compensation_token)
            except AdapterImportError as exc:
                # Domain publication already succeeded. The token is process-local
                # and expires with this request, so a failed revocation must not
                # undo a valid registry publication.
                current_app.logger.warning(
                    "adapter import compensation token revocation failed: %s",
                    exc.reason_code,
                )
        log_audit(
            "ml_intern_adapter_imported",
            {"adapter_id": imported_adapter_id, "actor": principal.subject},
        )
        return api_response(
            data=_adapter_read_model(record),
            code=201,
        )
    except AdapterImportError as exc:
        return _domain_error(exc)
    except RegistryError as exc:
        return _error("adapter_registry_conflict", str(exc), 409)


@ml_intern_training_bp.route("/evaluations", methods=["POST"])
@check_auth
@admin_required
def evaluate_adapter():
    try:
        idempotency_key = _idempotency_key()
        body = _json_body()
        unknown = sorted(
            set(body)
            - {"adapter_id", "dataset_id", "scorer_name", "live_confirmed", "risk_reason"}
        )
        if unknown:
            raise MlInternTrainingContractError(
                "evaluation_unknown_fields",
                f"unknown evaluation fields: {', '.join(unknown[:10])}",
            )
        adapter_id = str(body.get("adapter_id") or "")
        dataset_id = str(body.get("dataset_id") or "")
        services = _services()
        principal = _principal()
        record = services.registry.get(
            adapter_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
        if record is None:
            return _error("adapter_not_found", "adapter does not exist", 404)
        dataset = get_ml_intern_training_repository().get_dataset(_principal(), dataset_id)
        if dataset is None:
            return _error("dataset_not_found", "dataset does not exist", 404)
        if not dataset.validation_storage_ref or not bool(
            (dataset.validation_report or {}).get("ok", (dataset.validation_report or {}).get("valid", False))
        ):
            return _error(
                "evaluation_dataset_not_ready",
                "evaluation requires a validated validation split",
                409,
            )
        if record.status not in {"trained", "evaluated"}:
            return _error(
                "adapter_not_evaluable",
                "adapter must be trained and not yet approved, rejected, or deprecated",
                409,
            )
        mode = str(services.config.get("mode") or "dry_run")
        backend = "mock" if mode == "dry_run" else str(services.config.get("backend") or "peft_trl")
        risk_reason = body.get("risk_reason")
        if mode == "live":
            if body.get("live_confirmed") is not True:
                raise MlInternTrainingContractError(
                    "live_confirmation_required",
                    "live evaluation requires live_confirmed=true",
                    status_code=403,
                )
            if not isinstance(risk_reason, str) or not 8 <= len(risk_reason.strip()) <= 500:
                raise MlInternTrainingContractError(
                    "live_risk_reason_required",
                    "live evaluation requires a meaningful 8..500 character risk_reason",
                    status_code=403,
                )
        accepted, replayed = services.control.create_job(
            _principal(),
            {
                "dataset_id": dataset_id,
                "job_type": "evaluate_lora",
                "mode": mode,
                "backend": backend,
                "base_model": record.base_model,
                "method": record.method,
                "adapter_id": record.adapter_id,
                "scorer_name": str(body.get("scorer_name") or "generic"),
                **(
                    {"live_confirmed": True, "risk_reason": risk_reason.strip()}
                    if mode == "live" and isinstance(risk_reason, str)
                    else {}
                ),
                "hyperparameters": {
                    "seed": int(services.config.get("split_seed") or 42),
                    "batch_size": 1,
                    "max_seq_length": int(
                        (services.config.get("gpu_profile_defaults") or {}).get("max_seq_length") or 2048
                    ),
                },
            },
            idempotency_key=idempotency_key,
        )
        try:
            catalog_id = services.bridge.catalog_dataset_id(_principal(), dataset_id)
            services.catalog.mark_referenced(
                tenant_id=_principal().tenant_id,
                principal_id=_principal().subject,
                dataset_id=catalog_id,
                reference_id=str(accepted["id"]),
            )
        except (DatasetCatalogError, KeyError):
            pass
        if not replayed:
            log_audit(
                "ml_intern_adapter_evaluation_admitted",
                {
                    "evaluation_id": accepted["id"],
                    "adapter_id": adapter_id,
                    "dataset_id": dataset_id,
                    "mode": mode,
                    "actor": principal.subject,
                    "risk_reason_sha256": hashlib.sha256(str(risk_reason).strip().encode()).hexdigest()
                    if mode == "live"
                    else None,
                },
            )
        return api_response(
            data=_evaluation_read_model(
                accepted,
                adapter_id=adapter_id,
                dataset_id=dataset_id,
                minimum_score=float(services.config.get("minimum_eval_score") or 0.0),
            ),
            code=200 if replayed else 202,
        )
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)
    except RegistryError as exc:
        return _error("adapter_state_conflict", str(exc), 409)


@ml_intern_training_bp.route("/evaluations/<evaluation_id>", methods=["GET"])
@check_auth
@admin_required
def get_evaluation(evaluation_id: str):
    try:
        services = _services()
        principal = _principal()
        job = services.control.get_job(principal, evaluation_id)
        if job.get("job_type") != "evaluate_lora":
            return _error("evaluation_not_found", "evaluation does not exist", 404)
        try:
            return api_response(data=services.evaluation_store.get(principal, evaluation_id))
        except EvaluationStoreError:
            pass
        return api_response(
            data=_evaluation_read_model(
                job,
                adapter_id=str(job.get("adapter_id") or (job.get("configuration") or {}).get("adapter_id") or ""),
                dataset_id=str(job.get("dataset_id") or ""),
                minimum_score=float(services.config.get("minimum_eval_score") or 0.0),
            )
        )
    except MlInternTrainingContractError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/adapters/<adapter_id>/<action>", methods=["POST"])
@check_auth
@admin_required
def decide_adapter(adapter_id: str, action: str):
    try:
        idempotency_key = _idempotency_key()
        body = _json_body()
        if body.get("confirmed") is not True:
            return _error("adapter_decision_confirmation_required", "confirmed=true is required", 422)
        reason = str(body.get("reason") or "").strip()
        if len(reason) < 8:
            return _error("adapter_decision_reason_required", "a meaningful decision reason is required", 422)
        services = _services()
        principal = _principal()
        actor = principal.subject
        expected_version = _optional_expected_version(body)
        if action == "approve":
            record_before_approval = services.registry.get(
                adapter_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            )
            if record_before_approval is None:
                return _error("adapter_not_found", "adapter does not exist", 404)
            binding_error = _approval_evaluation_binding_error(record_before_approval)
            if binding_error is not None:
                return _error(
                    "adapter_evaluation_binding_mismatch",
                    binding_error,
                    409,
                )
            evaluation_job = get_ml_intern_training_repository().get_job(
                principal,
                str(record_before_approval.eval_report_ref or ""),
            )
            if evaluation_job is not None and evaluation_job.backend in UNSLOTH_BACKENDS:
                if expected_version is None:
                    return _error(
                        "promotion_revision_required",
                        "Unsloth promotion requires expected_version",
                        409,
                    )
                record, promotion_replayed = _unsloth_promotion_facade(
                    services
                ).promote(
                    principal,
                    record_before_approval,
                    expected_revision=expected_version,
                    idempotency_key=idempotency_key,
                    approved_by=actor,
                    reason=reason,
                    minimum_score=float(
                        services.config.get("minimum_eval_score") or 0.0
                    ),
                )
            else:
                promotion_replayed = False
                record = services.registry.approve(
                    adapter_id,
                    approved_by=actor,
                    reason=reason,
                    require_eval_report=services.config["require_eval_before_approval"],
                    minimum_eval_score=float(services.config.get("minimum_eval_score") or 0.0),
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    expected_version=expected_version,
                )
        elif action == "reject":
            record = services.registry.reject(
                adapter_id,
                reason=reason,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                expected_version=expected_version,
            )
        elif action == "deprecate":
            record = services.registry.deprecate(
                adapter_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                expected_version=expected_version,
            )
        elif action == "rollback":
            record, target = services.registry.rollback(
                adapter_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                expected_version=expected_version,
            )
        else:
            return _error("adapter_action_invalid", "adapter action is invalid", 404)
        log_audit(
            "ml_intern_adapter_decision",
            {
                "adapter_id": adapter_id,
                "action": action,
                "actor": actor,
                "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            },
        )
        response = _adapter_read_model(record)
        if action == "approve":
            response["promotion_replayed"] = promotion_replayed
        if action == "rollback":
            response["rollback_target"] = (
                {"type": "adapter", "adapter_id": target.adapter_id, "version": target.version}
                if target is not None
                else {"type": "base_model_only", "base_model_id": record.base_model}
            )
        return api_response(data=response)
    except RegistryVersionConflict as exc:
        return _error(exc.reason_code, str(exc), 409)
    except RegistryNotFoundError:
        return _error("adapter_not_found", "adapter does not exist", 404)
    except RegistryIdempotencyConflict as exc:
        return _error(exc.reason_code, str(exc), 409)
    except EvaluationStoreError as exc:
        return _error("adapter_promotion_evidence_unavailable", str(exc), 409)
    except RegistryError as exc:
        return _error("adapter_state_conflict", str(exc), 409)
    except PromotionGateError as exc:
        return _error(exc.code, str(exc), 409)


@ml_intern_training_bp.route("/adapters/<adapter_id>/export", methods=["POST"])
@check_auth
@admin_required
def export_adapter(adapter_id: str):
    try:
        _idempotency_key()
        principal = _principal()
        return api_response(
            data=_services().adapter_export.export(
                adapter_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            ),
            code=201,
        )
    except AdapterExportError as exc:
        return _domain_error(exc)


@ml_intern_training_bp.route("/exports/<artifact_id>", methods=["GET"])
@check_auth
@admin_required
def download_adapter_export(artifact_id: str):
    try:
        principal = _principal()
        path, digest = _services().adapter_export.resolve_export(
            artifact_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
        response = send_file(path, mimetype="application/zip", as_attachment=True, download_name=f"{artifact_id}.zip")
        response.headers["X-Artifact-SHA256"] = digest
        response.headers["Cache-Control"] = "no-store"
        return response
    except AdapterExportError as exc:
        return _domain_error(exc)


def _publish_imported_adapter(
    services: _TrainingServices,
    *,
    tenant_id: str,
    principal_id: str,
    result: Mapping[str, Any],
    display_name: str,
    method: str,
) -> AdapterRecord:
    """Publish one verified import into the domain registry or resume it."""

    adapter_id = str(result.get("adapter_id") or "")
    version = str(result.get("version") or "")
    base_model = str(result.get("base_model") or "")
    content_sha256 = str(result.get("content_sha256") or "")
    normalized_method = str(method or "lora").strip().lower() or "lora"
    artifact_path = services.adapter_import.resolve_artifact_path(
        tenant_id=tenant_id,
        principal_id=principal_id,
        adapter_id=adapter_id,
        version=version,
    )
    inspected = MlInternArtifactSecurityService(
        storage_root=services.config["artifact_root"]
    ).validate_adapter_tree(artifact_path)
    artifact_sha256 = str(inspected["tree_sha256"])
    return services.registry.register_trained(
        adapter_id=adapter_id,
        display_name=display_name,
        version=version,
        base_model=base_model,
        method=normalized_method,
        artifact_paths={"adapter_dir": str(artifact_path)},
        config_hash=content_sha256,
        artifact_sha256=artifact_sha256,
        notes="securely imported adapter; evaluation required",
        tenant_id=tenant_id,
        owner_subject=principal_id,
    )


def _adapter_import_publication_error(
    services: _TrainingServices,
    outcome: AdapterImportOutcome,
    publication_error: Exception,
):
    token = outcome.compensation_token
    if token is not None:
        try:
            services.adapter_import.compensate_import(token)
        except AdapterImportError as compensation_error:
            current_app.logger.error(
                "adapter import compensation failed after domain publication error: %s",
                compensation_error.reason_code,
                exc_info=(
                    type(compensation_error),
                    compensation_error,
                    compensation_error.__traceback__,
                ),
            )
            return _error(
                "adapter_import_compensation_failed",
                "adapter publication failed and its newly-created import could not be safely compensated",
                500,
                retryable=True,
            )
    if isinstance(publication_error, RegistryError):
        return _error("adapter_registry_conflict", str(publication_error), 409)
    if isinstance(publication_error, AdapterImportError):
        return _domain_error(publication_error)
    current_app.logger.error(
        "adapter import domain publication failed",
        exc_info=(type(publication_error), publication_error, publication_error.__traceback__),
    )
    return _error(
        "adapter_registry_write_failed",
        "adapter domain registry publication failed",
        500,
        retryable=True,
    )


@ml_intern_training_bp.route("/unsloth/storage", methods=["GET"])
@check_auth
@admin_required
def get_unsloth_storage():
    try:
        services = _services()
        principal = _principal()
        return api_response(
            data={
                "usage": services.storage.usage(
                    tenant_id=principal.tenant_id,
                    owner_scope_digest=tenant_scope_digest(principal),
                ),
                "items": services.storage.list_public(
                    tenant_id=principal.tenant_id,
                    owner_scope_digest=tenant_scope_digest(principal),
                ),
            }
        )
    except UnslothStorageError as exc:
        return _error(
            str(
                getattr(
                    exc,
                    "reason_code",
                    getattr(exc, "code", "unsloth_storage_rejected"),
                )
            ),
            str(exc),
            int(getattr(exc, "status_code", 409)),
        )


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
        audit_sink=log_audit,
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
        audit_sink=log_audit,
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
                credential_secret_ref=str(
                    security.get("auth_secret_ref") or ""
                ),
                expected_studio_version=str(
                    security.get("expected_studio_version") or ""
                ),
                allowed_hosts=tuple(security.get("allowed_hosts") or ()),
                allowed_ip_cidrs=tuple(
                    security.get("allowed_ip_cidrs") or ()
                ),
                external_network_enabled=bool(
                    services.config.get(
                        "external_network_allowed",
                        False,
                    )
                ),
                local_network_enabled=bool(
                    security.get("local_network_enabled")
                ),
                allow_plaintext_internal=not bool(
                    security.get("tls_required", True)
                ),
            )
        )
    except ValueError as exc:
        raise UnslothStudioTransportError(
            "unsloth_studio_configuration_invalid"
        ) from exc
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
            mcp_bearer_secret_ref=str(
                security.get("mcp_auth_secret_ref") or ""
            ),
            tool_policies=default_unsloth_mcp_tool_policies(),
            audit_sink=log_audit,
        )
    except ValueError as exc:
        raise UnslothMcpError(
            "unsloth_mcp_configuration_invalid"
        ) from exc
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
    capabilities = dict(
        raw_capabilities if isinstance(raw_capabilities, Mapping) else {}
    )
    raw_facets = capabilities.get("facets")
    facets = [
        dict(facet)
        for facet in raw_facets
        if isinstance(facet, Mapping)
    ] if isinstance(raw_facets, list) else []
    studio = dict(
        integration.get("studio")
        if isinstance(integration.get("studio"), Mapping)
        else {}
    )
    mcp = dict(
        integration.get("mcp")
        if isinstance(integration.get("mcp"), Mapping)
        else {}
    )
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
        available=(
            studio.get("available") is True
            and mcp.get("available") is True
        ),
        unavailable_reason="unsloth_mcp_client_unavailable",
        operations=tuple(
            str(tool_id)
            for tool_id in (
                mcp.get("tool_ids")
                if isinstance(mcp.get("tool_ids"), list)
                else ()
            )
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
        "reason_code": (
            None if available else unavailable_reason
        ),
        "source": "hub_policy",
        "operations": list(operations),
        "model_kinds": [],
    }
    return [
        *[
            facet
            for facet in facets
            if facet.get("id") != facet_id
        ],
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
        audit=CallableUnslothAuditAdapter(log_audit),
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
    configured_runtime = current_app.extensions.get(
        "unsloth_runtime_handoff_mutation_executor"
    )
    if configured_runtime is not None:
        if not (
            isinstance(configured_runtime, UnslothMutationExecutor)
            or isinstance(configured_runtime, UnslothOperationPayloadExecutor)
        ):
            return {"export": export_executor}
        runtime_executor = configured_runtime
    else:
        runtime_executor = build_runtime_handoff_mutation_executor(
            agent_config=dict(
                current_app.config.get("AGENT_CONFIG", {}) or {}
            ),
            export_service=services.adapter_export,
            adapter_registry=services.registry,
            storage_references=services.storage,
        )
    configured_cleanup = current_app.extensions.get(
        "unsloth_cleanup_mutation_executor"
    )
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
    ledger_path = (
        Path(services.config["artifact_root"])
        / ".control"
        / "unsloth-mutation-idempotency.sqlite3"
    )
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
    log_audit(
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


def _normalized_config() -> dict[str, Any]:
    agent = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    return normalize_ml_intern_training_config(
        {
            **dict(agent.get("ml_intern_training") or {}),
            **_environment_training_overrides(),
        }
    )


def _environment_training_overrides() -> dict[str, Any]:
    """Map the explicit container contract into the normal domain config."""

    result: dict[str, Any] = {}
    mappings = {
        "ANANTA_LORA_TRAINING_DATASET_ROOT": "dataset_root",
        "ANANTA_LORA_TRAINING_ARTIFACT_ROOT": "artifact_root",
        "ANANTA_LORA_TRAINING_DEFAULT_BACKEND": "backend",
        "ANANTA_LORA_TRAINING_GPU_PROFILE": "gpu_profile",
        "ANANTA_LORA_TRAINING_MODE": "mode",
    }
    for variable, key in mappings.items():
        value = str(os.getenv(variable, "")).strip()
        if value:
            result[key] = value
    enabled = str(os.getenv("ANANTA_LORA_TRAINING_ENABLED", "")).strip().casefold()
    if enabled:
        result["enabled"] = enabled in {"1", "true", "yes", "on"}
    catalog_json = str(os.getenv("ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON", "")).strip()
    if catalog_json:
        try:
            catalog = json.loads(catalog_json)
        except ValueError as exc:
            raise RuntimeError("LoRA training model catalog JSON is invalid") from exc
        if not isinstance(catalog, Mapping):
            raise RuntimeError("LoRA training model catalog must be an object")
        result["base_model_catalog"] = catalog
        result["base_models"] = [str(model_id) for model_id in catalog]
    studio_enabled = str(
        os.getenv("ANANTA_UNSLOTH_STUDIO_ENABLED", "")
    ).strip().casefold() in {"1", "true", "yes", "on"}
    if studio_enabled:
        mcp_enabled = str(
            os.getenv("ANANTA_UNSLOTH_STUDIO_MCP_ENABLED", "")
        ).strip().casefold() in {"1", "true", "yes", "on"}
        allowed_hosts = [
            value.strip()
            for value in str(
                os.getenv("ANANTA_UNSLOTH_STUDIO_ALLOWED_HOSTS", "")
            ).split(",")
            if value.strip()
        ]
        allowed_ip_cidrs = [
            value.strip()
            for value in str(
                os.getenv("ANANTA_UNSLOTH_STUDIO_ALLOWED_IP_CIDRS", "")
            ).split(",")
            if value.strip()
        ]
        result["unsloth_integration_enabled"] = True
        result["unsloth_security"] = {
            "operating_mode": "studio_managed",
            "studio_url": str(
                os.getenv("ANANTA_UNSLOTH_STUDIO_URL", "")
            ).strip(),
            "allowed_hosts": allowed_hosts,
            "allowed_ip_cidrs": allowed_ip_cidrs,
            "auth_secret_ref": "env://ANANTA_UNSLOTH_STUDIO_PASSWORD",
            "expected_studio_version": str(
                os.getenv("ANANTA_UNSLOTH_STUDIO_EXPECTED_VERSION", "")
            ).strip(),
            "tls_required": False,
            "local_network_enabled": True,
            "mcp_enabled": mcp_enabled,
            "mcp_auth_secret_ref": (
                "env://ANANTA_UNSLOTH_STUDIO_MCP_TOKEN"
                if mcp_enabled
                else None
            ),
        }
    return result


def _principal() -> MlInternTrainingPrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "hub-admin").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return MlInternTrainingPrincipal(tenant_id=tenant, subject=subject)


def _submit_legacy_job(payload: dict[str, Any]):
    cfg = _normalized_config()
    requested_mode = str(payload.get("mode") or "dry_run").strip().lower()
    requested_backend = str(payload.get("backend") or "mock").strip().lower()
    if cfg.get("mode") != "dry_run" or requested_mode != "dry_run" or requested_backend != "mock":
        return _error(
            "legacy_live_execution_forbidden",
            "dataset_path compatibility requests are restricted to the explicit mock dry-run contract",
            409,
        )
    result = get_training_job_service({**cfg, "mode": "dry_run", "backend": "mock"}).submit_job(payload)
    code = 202 if result.status in {"dry_run_completed", "completed", "trained"} else 400
    if result.status == "disabled":
        code = 403
    return api_response(data=result.to_dict(), code=code)


def _approval_evaluation_binding_error(record: AdapterRecord) -> str | None:
    reference = str(record.eval_report_ref or "")
    if not reference:
        return "approval requires a persisted evaluation job"
    principal = _principal()
    job = get_ml_intern_training_repository().get_job(principal, reference)
    if job is None or job.status != "completed" or job.job_type != "evaluate_lora":
        return "evaluation reference is not a completed evaluation job for this principal"
    correlated_adapter = str(job.adapter_id or (job.request_spec or {}).get("adapter_id") or "")
    if correlated_adapter != record.adapter_id or str(job.base_model or "") != record.base_model:
        return "evaluation job does not match the adapter and base model"
    return None


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise MlInternTrainingContractError("invalid_json", "JSON object body is required", status_code=400)
    return value


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(value) <= 256 or any(character.isspace() for character in value):
        raise MlInternTrainingContractError(
            "idempotency_key_invalid",
            "Idempotency-Key must contain 8..256 non-whitespace characters",
            status_code=400,
        )
    return value


def _bounded_query_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        result = int(raw)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError(
            "query_parameter_invalid", f"{name} must be an integer"
        ) from exc
    if not minimum <= result <= maximum:
        raise MlInternTrainingContractError(
            "query_parameter_out_of_bounds",
            f"{name} must be between {minimum} and {maximum}",
        )
    return result


def _cursor_offset(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        offset = int(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("cursor_invalid", "cursor must be a non-negative integer") from exc
    if not 0 <= offset <= 10_000_000:
        raise MlInternTrainingContractError("cursor_invalid", "cursor is outside its supported range")
    return offset


def _form_float(name: str, default: float) -> float:
    try:
        result = float(request.form.get(name, default))
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError(
            "multipart_field_invalid", f"{name} must be numeric"
        ) from exc
    if not math.isfinite(result):
        raise MlInternTrainingContractError("multipart_field_invalid", f"{name} must be finite")
    return result


def _form_int(name: str, default: int) -> int:
    try:
        return int(request.form.get(name, default))
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError(
            "multipart_field_invalid", f"{name} must be an integer"
        ) from exc


def _bounded_body_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise MlInternTrainingContractError("numeric_value_out_of_bounds", "numeric value is outside safe bounds")
    return parsed


def _optional_expected_version(body: Mapping[str, Any]) -> int | None:
    if "expected_version" not in body:
        return None
    value = body.get("expected_version")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise MlInternTrainingContractError(
            "adapter_expected_version_invalid",
            "expected_version must be a positive integer",
        )
    return value


def _bounded_body_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be numeric") from exc
    if not minimum <= parsed <= maximum:
        raise MlInternTrainingContractError("numeric_value_out_of_bounds", "numeric value is outside safe bounds")
    return parsed


def _dataset_detail(projected: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(projected)
    result["validation_report"] = _validation_read_model(str(projected["id"]), report) if report else None
    return result


def _validation_read_model(dataset_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
    train = report.get("train") if isinstance(report.get("train"), Mapping) else report
    validation = report.get("validation") if isinstance(report.get("validation"), Mapping) else {}
    errors = list(train.get("errors") or []) + list(validation.get("errors") or [])
    warnings = list(train.get("warnings") or []) + list(validation.get("warnings") or [])
    issues = []
    for severity, rows in (("error", errors), ("warning", warnings)):
        for row in rows[:200]:
            item = row if isinstance(row, Mapping) else {"type": str(row)}
            issues.append(
                {
                    "code": str(item.get("type") or item.get("code") or "validation_issue")[:128],
                    "severity": severity,
                    "record_index": item.get("line"),
                    "field": item.get("field"),
                    "message": str(item.get("message") or "")[:256] or None,
                    "redacted": True,
                }
            )
    train_records = int(train.get("accepted_record_count") or train.get("line_count") or 0)
    validation_records = int(validation.get("accepted_record_count") or validation.get("line_count") or 0)
    duplicate_records = int(train.get("duplicate_count") or 0) + int(validation.get("duplicate_count") or 0)

    def partition_summary(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sha256": str(value.get("dataset_hash") or "")[:64] or None,
            "format": str(value.get("format_type") or "")[:32] or None,
            "total_records": int(value.get("total_lines") or 0),
            "accepted_records": int(value.get("accepted_record_count") or 0),
            "rejected_records": int(value.get("rejected_record_count") or 0),
            "duplicate_records": int(value.get("duplicate_count") or 0),
            "secret_scan_passed": bool(value.get("secret_scan_passed", False)),
            "error_count": int(value.get("error_count") or len(value.get("errors") or [])),
            "warning_count": int(value.get("warning_count") or len(value.get("warnings") or [])),
        }

    return {
        "schema": str(report.get("schema") or "mlintern_dataset_catalog_validation.v1")[:128],
        "dataset_id": dataset_id,
        "valid": bool(report.get("ok", report.get("valid", False))),
        "trainable": bool(report.get("ok", report.get("valid", False))) and validation_records > 0,
        "total_records": train_records + validation_records,
        "accepted_records": train_records + validation_records,
        "rejected_records": len(errors),
        "duplicate_records": duplicate_records,
        "secret_findings": len(train.get("secret_findings") or []) + len(validation.get("secret_findings") or []),
        "pii_findings": int(report.get("pii_finding_count") or 0),
        "train_records": train_records,
        "validation_records": validation_records,
        "reason_codes": [str(value)[:128] for value in list(report.get("reason_codes") or [])[:100]],
        "pair_errors": [str(value)[:256] for value in list(report.get("pair_errors") or [])[:100]],
        "semantic_overlap_count": int(report.get("semantic_overlap_count") or 0),
        "partitions": {
            "train": partition_summary(train),
            "validation": partition_summary(validation) if validation else None,
        },
        "issues": issues,
        "generated_at": report.get("validated_at"),
    }


def _adapter_read_model(record: AdapterRecord | None) -> dict[str, Any]:
    if record is None:
        raise RegistryError("adapter not found")
    raw_path = record.artifact_paths.get("adapter_dir") or record.artifact_paths.get("adapter_path")
    exists = bool(raw_path and Path(raw_path).is_dir())
    hash_verified = False
    if exists and record.artifact_sha256:
        try:
            inspected = MlInternArtifactSecurityService(
                storage_root=_normalized_config()["artifact_root"]
            ).validate_adapter_tree(Path(str(raw_path)))
            hash_verified = inspected["tree_sha256"] == record.artifact_sha256
        except Exception:
            hash_verified = False
    return {
        "id": record.adapter_id,
        "name": record.display_name,
        "version": int(record.version) if str(record.version).isdigit() else 1,
        "adapter_version": record.version,
        "registry_version": record.registry_version,
        "base_model_id": record.base_model,
        "method": record.method,
        "status": record.status,
        "score": record.eval_score,
        "active": record.status == "approved",
        "sha256": record.artifact_sha256,
        "hash_verified": hash_verified,
        "artifact_exists": exists,
        "evaluation_id": record.eval_report_ref,
        "promotion_count": len(record.promotion_history),
        "latest_promotion": (
            {
                "promotion_id": record.promotion_history[-1].get("promotion_id"),
                "evaluation_id": record.promotion_history[-1].get("evaluation_id"),
                "registry_revision": record.promotion_history[-1].get(
                    "revision_after"
                ),
                "created_at": record.promotion_history[-1].get("created_at"),
            }
            if record.promotion_history
            else None
        ),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _adapter_import_read_model(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("adapter_id"),
        "name": item.get("display_name") or item.get("adapter_id"),
        "version": int(item.get("version")) if str(item.get("version")).isdigit() else 1,
        "base_model_id": item.get("base_model"),
        "method": item.get("method"),
        "status": item.get("status"),
        "sha256": item.get("content_sha256"),
        "size_bytes": item.get("total_bytes"),
        "active": False,
        "hash_verified": True,
        "artifact_exists": True,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _evaluation_read_model(
    job: Mapping[str, Any],
    *,
    adapter_id: str,
    dataset_id: str,
    minimum_score: float = 0.0,
) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
    raw_metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
    base = raw_metrics.get("base") if isinstance(raw_metrics.get("base"), Mapping) else {}
    adapter = raw_metrics.get("adapter") if isinstance(raw_metrics.get("adapter"), Mapping) else {}
    metrics: list[dict[str, Any]] = []
    for name in sorted(set(base) & set(adapter)):
        base_value = base.get(name)
        adapter_value = adapter.get(name)
        if (
            isinstance(base_value, bool)
            or isinstance(adapter_value, bool)
            or not isinstance(base_value, (int, float))
            or not isinstance(adapter_value, (int, float))
        ):
            continue
        lower_is_better = name in {"eval_loss", "loss", "perplexity"}
        delta = float(adapter_value) - float(base_value)
        metrics.append(
            {
                "name": str(name)[:128],
                "base_value": float(base_value),
                "adapter_value": float(adapter_value),
                "delta": delta,
                "higher_is_better": not lower_is_better,
                "threshold": 0.0,
                "passed": delta <= 0 if lower_is_better else delta >= 0,
            }
        )
    try:
        decision = evaluate_adapter_metrics(raw_metrics, minimum_score=minimum_score)
    except ValueError:
        decision = None
    samples = raw_metrics.get("samples") if isinstance(raw_metrics.get("samples"), list) else []
    error = job.get("error") if isinstance(job.get("error"), Mapping) else {}
    status = str(job.get("status") or "queued")
    completed_decision = decision if status == "completed" else None
    return {
        "id": str(job.get("id") or job.get("job_id") or ""),
        "adapter_id": adapter_id,
        "dataset_id": dataset_id,
        "status": status,
        "passed": completed_decision.passed if completed_decision else None,
        "aggregate_score": completed_decision.score if completed_decision else None,
        "metrics": metrics,
        "samples": samples[:100],
        "reason_code": error.get("code") or (
            completed_decision.reason_code if completed_decision else None
        ),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
    }


def _domain_error(exc: Exception):
    reason = str(getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "ml_intern_training_error")
    status = int(getattr(exc, "status_code", 0) or getattr(exc, "http_status", 0) or _status_for_reason(reason))
    return _error(reason, str(exc), status, retryable=bool(getattr(exc, "retryable", status >= 500)))


def _status_for_reason(reason: str) -> int:
    if "not_found" in reason or "does not exist" in reason:
        return 404
    if "conflict" in reason or "referenced" in reason or "transition" in reason:
        return 409
    if "quota" in reason or "too_large" in reason:
        return 413
    if "unavailable" in reason or "worker_required" in reason:
        return 503
    return 422


def _error(reason: str, message: str, status: int, *, retryable: bool = False):
    return api_response(
        status="error",
        code=status,
        data={"error": {"code": reason, "message": message, "retryable": retryable}},
    )
