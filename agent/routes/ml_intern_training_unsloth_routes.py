"""Unsloth integration HTTP routes registered on the ML-Intern blueprint."""

from __future__ import annotations

import json
from typing import Mapping

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.routes.ml_intern_training_route_support import (
    _domain_error,
    _error,
    _idempotency_key,
    _json_body,
    _principal,
)
from agent.routes.ml_intern_training_unsloth_support import (
    _audit_unsloth_mutation,
    _services,
    _unsloth_evidence,
    _unsloth_integration_probe,
    _unsloth_mcp_adapter,
    _unsloth_model_registry,
    _unsloth_model_source_adapter,
    _unsloth_model_source_request,
    _unsloth_mutation_service,
    _unsloth_tasks,
)
from agent.services.ml_intern_training_config_service import (
    MlInternTrainingConfigError,
)
from agent.services.ml_intern_training_contract import (
    MlInternTrainingContractError,
)
from agent.services.ml_intern_training_repository_provider import (
    get_ml_intern_training_repository,
)
from agent.services.unsloth_completion_outbox_service import (
    get_unsloth_completion_outbox_reconciler,
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
from agent.services.unsloth_evidence import EvidenceVerificationError
from agent.services.unsloth_mcp_adapter import (
    UnslothMcpError,
)
from agent.services.unsloth_model_source_adapter import (
    ModelSourceValidationError,
)
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationError,
)
from agent.services.unsloth_storage_governance_service import (
    UnslothStorageError,
)
from agent.services.unsloth_studio_transport import (
    UnslothStudioTransportError,
)
from ananta_contracts.model_catalog import ModelCatalog


def register_unsloth_routes(blueprint: Blueprint) -> None:
    """Attach Unsloth endpoints while preserving the public parent blueprint."""

    @blueprint.route(
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

    @blueprint.route(
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
                confirmation_id=(str(body.get("confirmation_id") or "").strip() or None),
                idempotency_key=(str(request.headers.get("Idempotency-Key") or "").strip() or None),
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
            return _error(
                getattr(
                    exc,
                    "reason_code",
                    "unsloth_studio_upstream_unavailable",
                ),
                "Unsloth Studio upstream is unavailable",
                503,
                retryable=True,
            )
        except UnslothMcpError as exc:
            status = (
                503
                if exc.reason_code
                in {
                    "incompatible_upstream_contract",
                    "unsloth_mcp_probe_unavailable",
                    "unsloth_mcp_upstream_unavailable",
                    "unsloth_mcp_bearer_secret_unavailable",
                    "unsloth_mcp_configuration_invalid",
                }
                else 409
            )
            return _error(
                exc.reason_code,
                "MCP request was rejected by the Hub policy boundary",
                status,
                retryable=status == 503,
            )

    @blueprint.route(
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
            resource_id = str(body.get("resource_id") or "").strip()[:128] or None
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
            code = 200 if bool(result["dry_run"]) or bool(result.get("replayed", False)) else 201
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

    @blueprint.route("/unsloth/model-imports/plan", methods=["POST"])
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

    @blueprint.route("/unsloth/model-imports", methods=["POST"])
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

    @blueprint.route("/unsloth/model-imports/<task_id>/result", methods=["POST"])
    @check_auth
    @admin_required
    def complete_unsloth_model_import(task_id: str):
        return _error(
            "model_import_direct_completion_gone",
            ("Model imports complete only through the validated Hub-to-Worker task result path."),
            410,
        )

    @blueprint.route("/unsloth/models", methods=["GET"])
    @check_auth
    @admin_required
    def list_unsloth_imported_models():
        get_unsloth_completion_outbox_reconciler().reconcile_pending(limit=100)
        records = _unsloth_model_registry(_services()).list_versions(tenant_id=_principal().tenant_id)
        revision = max((record.catalog_revision for record in records), default=None)
        return api_response(
            data=ModelCatalog(
                catalog_revision=revision,
                imported_models=records,
            ).to_wire()
        )

    @blueprint.route("/unsloth/data-recipes", methods=["POST"])
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
