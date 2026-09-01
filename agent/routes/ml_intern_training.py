from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping

from flask import Blueprint, Response, current_app, g, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import MlInternDatasetDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepositoryConflict,
    get_ml_intern_training_repository,
)
from agent.routes.ml_intern_training_adapter_routes import register_adapter_routes
from agent.routes.ml_intern_training_route_support import (
    _bounded_body_float,
    _bounded_body_int,
    _bounded_query_int,
    _cursor_offset,
    _dataset_detail,
    _domain_error,
    _error,
    _form_float,
    _form_int,
    _idempotency_key,
    _json_body,
    _normalized_config,
    _principal,
    _submit_legacy_job,
    _validation_read_model,
)
from agent.routes.ml_intern_training_unsloth_routes import register_unsloth_routes
from agent.routes.ml_intern_training_unsloth_support import (
    _compose_unsloth_integration_facets,
    _services,
    _unsloth_confirmation_secret,
    _unsloth_integration_probe,
    _unsloth_mutation_executors,
)
from agent.services.ml_intern_backend_selection_service import (
    BackendSelectionError,
    BackendSelectionRequest,
    MlInternBackendSelectionService,
)
from agent.services.ml_intern_dataset_catalog_service import (
    DatasetCatalogError,
)
from agent.services.ml_intern_dataset_preview_service import (
    DatasetPreviewError,
)
from agent.services.ml_intern_dataset_split_service import (
    DatasetSplitError,
)
from agent.services.ml_intern_training_contract import (
    BACKENDS,
    JOB_STATUSES,
    MlInternTrainingContractError,
)
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.unsloth_mutation_command_service import (
    project_unsloth_capabilities,
)
from agent.services.unsloth_storage_governance_service import (
    UnslothStorageError,
    tenant_scope_digest,
)

ml_intern_training_bp = Blueprint("ml_intern_training", __name__, url_prefix="/api/ml-intern-training")
_MULTIPART_OVERHEAD_BYTES = 512 * 1024
_ROUTE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_DEFERRED_TRAINING_DISPATCH = "_ml_intern_training_dispatch"

register_unsloth_routes(ml_intern_training_bp)
register_adapter_routes(ml_intern_training_bp)


@ml_intern_training_bp.teardown_request
def _dispatch_admitted_training_job(_error: BaseException | None) -> None:
    """Start an admitted job only after all request audit writes have ended."""

    pending = g.pop(_DEFERRED_TRAINING_DISPATCH, None)
    if pending is None:
        return
    control, principal, job_id = pending
    try:
        control.schedule_reconciled_job(principal, job_id)
    except Exception:
        # Admission is already durable. The Hub reconciler will automatically
        # offer this queued job again; teardown must never mask the response.
        return


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
                tuple(_unsloth_mutation_executors(services)) if _unsloth_confirmation_secret() is not None else ()
            ),
        )
        dendritic = current_app.extensions.get("dendritic_memory_capabilities")
        result["dendritic_memory_experiment"] = (
            dendritic.projection()
            if dendritic is not None
            else {
                "schema": "ananta.dendritic-memory-capability.v1",
                "state": "disabled",
                "available": False,
                "reason_code": "dendritic_experiment_not_configured",
                "experimental": True,
                "not_production_ready": True,
                "claims_not_verified": True,
                "human_intervention_required": False,
            }
        )
        research = current_app.extensions.get("research_training_capabilities")
        result["research_training"] = (
            research.projection()
            if research is not None
            else {
                "schema": "ananta.research-training-capability.v1",
                "state": "disabled",
                "available": False,
                "reason_code": "research_training_not_configured",
                "mode": "disabled",
                "automatic_release_enabled": False,
                "worker": {
                    "state": "unavailable",
                    "reason_code": "research_worker_not_reported",
                    "engine_version": None,
                    "capabilities": [],
                    "gpu_profiles": [],
                    "network_probe_performed": False,
                },
                "experimental": True,
                "not_production_ready": True,
                "claims_not_verified": True,
                "human_intervention_required": False,
            }
        )
        return api_response(data=result)
    except (RuntimeError, ValueError) as exc:
        return _error("training_runtime_configuration_invalid", str(exc), 503, retryable=True)


@ml_intern_training_bp.route("/backends/recommendation", methods=["POST"])
@check_auth
@admin_required
def recommend_backend():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("invalid_json", "JSON object body is required", 400)
    try:
        backends = _services().control.capabilities()["backends"]
        recommendation = MlInternBackendSelectionService().recommend(
            BackendSelectionRequest.from_mapping(payload),
            backends=backends,
        )
        return api_response(data=recommendation)
    except BackendSelectionError as exc:
        return _error(exc.reason_code, str(exc), 422)


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
        accepted, replayed = services.control.admit_job(
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
                    "risk_reason_sha256": hashlib.sha256(risk_reason.encode()).hexdigest() if risk_reason else None,
                },
            )
            # Defer dispatch beyond Flask's global after_request audit. The
            # blueprint teardown hook runs after those request-owned database
            # writes, while the persisted queue provides automatic recovery.
            setattr(
                g,
                _DEFERRED_TRAINING_DISPATCH,
                (services.control, principal, str(accepted["id"])),
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
