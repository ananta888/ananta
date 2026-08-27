"""Adapter lifecycle HTTP routes registered on the ML-Intern blueprint."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from flask import Blueprint, current_app, request, send_file

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.routes.ml_intern_training_route_support import (
    _adapter_import_read_model,
    _adapter_read_model,
    _approval_evaluation_binding_error,
    _bounded_query_int,
    _domain_error,
    _error,
    _evaluation_read_model,
    _idempotency_key,
    _json_body,
    _optional_expected_version,
    _principal,
    _route_audit_sink,
)
from agent.routes.ml_intern_training_unsloth_support import (
    _services,
    _TrainingServices,
    _unsloth_promotion_facade,
)
from agent.services.local_adapter_release_target import (
    requires_governed_local_release,
)
from agent.services.ml_intern_adapter_export_service import (
    AdapterExportError,
)
from agent.services.ml_intern_adapter_import_service import (
    AdapterImportError,
    AdapterImportOutcome,
)
from agent.services.ml_intern_adapter_registry_service import (
    AdapterRecord,
    RegistryError,
    RegistryIdempotencyConflict,
    RegistryNotFoundError,
    RegistryVersionConflict,
)
from agent.services.ml_intern_artifact_security_service import (
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_catalog_service import (
    DatasetCatalogError,
)
from agent.services.ml_intern_evaluation_promotion_facade import (
    PromotionGateError,
)
from agent.services.ml_intern_evaluation_store_service import (
    EvaluationStoreError,
)
from agent.services.ml_intern_training_contract import (
    UNSLOTH_BACKENDS,
    MlInternTrainingContractError,
)
from agent.services.ml_intern_training_repository_provider import (
    get_ml_intern_training_repository,
)


def register_adapter_routes(blueprint: Blueprint) -> None:  # noqa: C901
    """Attach adapter endpoints while preserving the public parent blueprint."""

    @blueprint.route("/adapters", methods=["GET"])
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
        existing = {(row["id"], str(row.get("adapter_version") or row["version"])) for row in rows}
        for item in imports:
            key = (str(item["adapter_id"]), str(item["version"]))
            if key not in existing:
                rows.append(_adapter_import_read_model(item))
        limit = _bounded_query_int("limit", 100, minimum=1, maximum=200)
        return api_response(data={"items": rows[:limit], "count": min(len(rows), limit), "total": len(rows)})

    @blueprint.route("/adapters/import", methods=["POST"])
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
            _route_audit_sink(
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

    @blueprint.route("/evaluations", methods=["POST"])
    @check_auth
    @admin_required
    def evaluate_adapter():
        try:
            idempotency_key = _idempotency_key()
            body = _json_body()
            unknown = sorted(set(body) - {"adapter_id", "dataset_id", "scorer_name", "live_confirmed", "risk_reason"})
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
                _route_audit_sink(
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

    @blueprint.route("/evaluations/<evaluation_id>", methods=["GET"])
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

    @blueprint.route("/adapters/<adapter_id>/<action>", methods=["POST"])
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
                if requires_governed_local_release(record_before_approval.release_target):
                    return _error(
                        "local_adapter_governed_release_required",
                        "local runtime candidates require offline, shadow, canary, and atomic release gates",
                        409,
                    )
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
                    record, promotion_replayed = _unsloth_promotion_facade(services).promote(
                        principal,
                        record_before_approval,
                        expected_revision=expected_version,
                        idempotency_key=idempotency_key,
                        approved_by=actor,
                        reason=reason,
                        minimum_score=float(services.config.get("minimum_eval_score") or 0.0),
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
            _route_audit_sink(
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

    @blueprint.route("/adapters/<adapter_id>/export", methods=["POST"])
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

    @blueprint.route("/exports/<artifact_id>", methods=["GET"])
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
            response = send_file(
                path,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{artifact_id}.zip",
            )
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
