"""Authenticated Hub API for the bounded spreadsheet studio slice."""

from __future__ import annotations

import hashlib
import hmac
import io
import os
from collections.abc import Callable
from typing import Any

from flask import Blueprint, Response, current_app, request, send_file

from agent.auth import admin_required, check_user_auth, get_request_auth_context
from agent.common.errors import api_response
from agent.services.ml_intern_adapter_registry_contract import (
    RegistryIdempotencyConflict,
    RegistryVersionConflict,
)
from agent.services.ml_intern_lora_inference_service import get_lora_inference_service
from agent.services.spreadsheet_consent_revocation_coordinator import (
    SpreadsheetConsentRevocationCoordinator,
)
from agent.services.spreadsheet_inference_service import SpreadsheetInferenceService
from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningConflict
from agent.services.spreadsheet_ml_intern_bridge_service import SpreadsheetMlInternBridgeService
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import SpreadsheetContractError

spreadsheet_studio_bp = Blueprint("spreadsheet_studio", __name__, url_prefix="/api/spreadsheet-studio")


def _service():
    value = current_app.extensions.get("spreadsheet_studio_service")
    if value is None:
        raise RuntimeError("spreadsheet_studio_unavailable")
    return value


def _learning_service():
    value = current_app.extensions.get("spreadsheet_learning_service")
    if value is None:
        raise RuntimeError("spreadsheet_learning_unavailable")
    return value


def _training_admission_service():
    value = current_app.extensions.get("spreadsheet_training_admission_service")
    if value is None:
        raise RuntimeError("spreadsheet_training_admission_unavailable")
    return value


def _adapter_admission_service():
    value = current_app.extensions.get("spreadsheet_adapter_admission_service")
    if value is None:
        raise RuntimeError("spreadsheet_adapter_admission_unavailable")
    return value


def _proposal_execution_service():
    return current_app.extensions.get("spreadsheet_proposal_execution_service") or _service()


def _proposal_job_service():
    value = current_app.extensions.get("spreadsheet_proposal_execution_service")
    if value is None:
        raise RuntimeError("spreadsheet_proposal_jobs_unavailable")
    return value


def _execution_ingress_service():
    value = current_app.extensions.get("spreadsheet_execution_ingress_service")
    if value is None:
        raise RuntimeError("spreadsheet_execution_ingress_unavailable")
    return value


def _operations_service():
    value = current_app.extensions.get("spreadsheet_operations_service")
    if value is None:
        raise RuntimeError("spreadsheet_operations_unavailable")
    return value


def _bearer_token() -> str:
    authorization = str(request.headers.get("Authorization") or "")
    return authorization[7:] if authorization.startswith("Bearer ") else ""


def _worker_authenticated() -> bool:
    expected = str(
        current_app.config.get("ANANTA_SPREADSHEET_WORKER_TOKEN") or os.getenv("ANANTA_SPREADSHEET_WORKER_TOKEN") or ""
    ).strip()
    supplied = _bearer_token()
    return len(expected) >= 24 and hmac.compare_digest(supplied, expected)


def _identity() -> tuple[str, str]:
    identity = dict(get_request_auth_context() or {})
    principal = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or principal).strip()
    if not principal or not tenant:
        raise PermissionError("spreadsheet_principal_invalid")
    tenant_id = f"tenant-{hashlib.sha256(tenant.encode()).hexdigest()[:32]}"
    principal_id = f"principal-{hashlib.sha256(principal.encode()).hexdigest()[:32]}"
    return tenant_id, principal_id


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("spreadsheet_payload_invalid")
    return value


def _invoke(operation: Callable[[], Any], *, created: bool = False, accepted: bool = False):
    try:
        return api_response(data=operation(), code=202 if accepted else (201 if created else 200))
    except SpreadsheetStoreConflict as exc:
        return api_response(status="error", message=str(exc), code=409)
    except SpreadsheetLearningConflict as exc:
        return api_response(status="error", message=str(exc), code=409)
    except (RegistryIdempotencyConflict, RegistryVersionConflict) as exc:
        return api_response(status="error", message=str(exc), code=409)
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (SpreadsheetContractError, TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@spreadsheet_studio_bp.get("/capabilities")
@check_user_auth
def capabilities():
    def operation():
        service = current_app.extensions.get("spreadsheet_studio_service")
        if service is not None:
            return service.capabilities()
        status = current_app.extensions.get("spreadsheet_studio_wiring_status")
        return {
            "schema": "ananta.spreadsheet-studio-capability.v1",
            "available": False,
            "state": "disabled",
            "mode": "disabled",
            "automatic_promotion_enabled": False,
            "executor": {"state": "unavailable"},
            "supported_formats": [],
            "supported_snapshot_schemas": [],
            "actual_diff_schema": "ananta.spreadsheet-actual-diff.v1",
            "validation_result_schema": "ananta.spreadsheet-validation-result.v2",
            "validation_reference_schema": "ananta.spreadsheet-validation-reference.v1",
            "libreoffice_fidelity_verified": False,
            "training_available": False,
            "source_grounding_verified": False,
            "reason_code": getattr(status, "reason_code", "spreadsheet_studio_disabled"),
            "human_intervention_required": False,
        }

    return _invoke(operation)


@spreadsheet_studio_bp.post("/documents")
@check_user_auth
def create_document():
    def operation():
        body = _body()
        return _service().create_document(
            tenant_id=_identity()[0],
            owner_id=_identity()[1],
            title=body.get("title"),
            snapshot=body.get("snapshot") or {},
            document_id=body.get("document_id"),
        )

    return _invoke(operation, created=True)


@spreadsheet_studio_bp.post("/documents/import")
@check_user_auth
def import_document():
    def operation():
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            raise ValueError("spreadsheet_upload_missing")
        content = uploaded.stream.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("spreadsheet_upload_size_invalid")
        filename = str(uploaded.filename)
        default_title = filename.rsplit(".", 1)[0]
        tenant_id, principal_id = _identity()
        return _service().import_document(
            tenant_id=tenant_id,
            owner_id=principal_id,
            title=request.form.get("title") or default_title,
            filename=filename,
            media_type=str(uploaded.mimetype or ""),
            content=content,
            document_id=request.form.get("document_id"),
        )

    return _invoke(operation, created=True)


@spreadsheet_studio_bp.get("/documents")
@check_user_auth
def list_documents():
    return _invoke(
        lambda: _service().list_documents(
            tenant_id=_identity()[0],
            principal_id=_identity()[1],
            limit=int(request.args.get("limit") or 100),
        )
    )


@spreadsheet_studio_bp.get("/documents/<document_id>")
@check_user_auth
def get_document(document_id: str):
    return _invoke(
        lambda: _service().get_document(tenant_id=_identity()[0], document_id=document_id, principal_id=_identity()[1])
    )


@spreadsheet_studio_bp.get("/documents/<document_id>/viewport")
@check_user_auth
def get_document_viewport(document_id: str):
    return _invoke(
        lambda: _service().get_viewport(
            tenant_id=_identity()[0],
            document_id=document_id,
            principal_id=_identity()[1],
            sheet_id=str(request.args.get("sheet_id") or ""),
            start=str(request.args.get("start") or "A1"),
            end=str(request.args.get("end") or "Z100"),
            offset=int(request.args.get("offset") or 0),
            limit=int(request.args.get("limit") or 1_000),
        )
    )


@spreadsheet_studio_bp.post("/validation-references")
@check_user_auth
def create_validation_reference():
    def operation():
        body = _body()
        if set(body) != {"reference_id", "document_id", "version"}:
            raise ValueError("spreadsheet_validation_reference_fields_invalid")
        tenant_id, principal_id = _identity()
        return _service().create_validation_reference(
            tenant_id=tenant_id,
            principal_id=principal_id,
            reference_id=body["reference_id"],
            document_id=body["document_id"],
            version=body["version"],
        )

    return _invoke(operation, created=True)


@spreadsheet_studio_bp.get("/validation-references")
@check_user_auth
def list_validation_references():
    return _invoke(
        lambda: _service().list_validation_references(
            tenant_id=_identity()[0],
            principal_id=_identity()[1],
            limit=int(request.args.get("limit") or 100),
        )
    )


@spreadsheet_studio_bp.get("/validation-references/<reference_id>")
@check_user_auth
def get_validation_reference(reference_id: str):
    return _invoke(
        lambda: _service().get_validation_reference(
            tenant_id=_identity()[0],
            principal_id=_identity()[1],
            reference_id=reference_id,
        )
    )


@spreadsheet_studio_bp.get("/documents/<document_id>/versions")
@check_user_auth
def list_document_versions(document_id: str):
    return _invoke(
        lambda: _service().list_versions(
            tenant_id=_identity()[0],
            document_id=document_id,
            principal_id=_identity()[1],
            limit=int(request.args.get("limit") or 100),
        )
    )


@spreadsheet_studio_bp.get("/documents/<document_id>/versions/<int:version>")
@check_user_auth
def get_document_version(document_id: str, version: int):
    return _invoke(
        lambda: _service().get_version(
            tenant_id=_identity()[0],
            document_id=document_id,
            version=version,
            principal_id=_identity()[1],
        )
    )


@spreadsheet_studio_bp.get("/documents/<document_id>/versions/<int:version>/viewport")
@check_user_auth
def get_document_version_viewport(document_id: str, version: int):
    return _invoke(
        lambda: _service().get_version_viewport(
            tenant_id=_identity()[0],
            document_id=document_id,
            version=version,
            principal_id=_identity()[1],
            sheet_id=str(request.args.get("sheet_id") or ""),
            start=str(request.args.get("start") or "A1"),
            end=str(request.args.get("end") or "Z100"),
            offset=int(request.args.get("offset") or 0),
            limit=int(request.args.get("limit") or 1_000),
        )
    )


@spreadsheet_studio_bp.get("/documents/<document_id>/original")
@check_user_auth
def download_original(document_id: str):
    try:
        tenant_id, principal_id = _identity()
        content, source = _service().download_original(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        return send_file(
            io.BytesIO(content),
            mimetype=str(source["media_type"]),
            as_attachment=True,
            download_name=f"{document_id}.{source['format']}",
            max_age=0,
        )
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (RuntimeError, TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)


@spreadsheet_studio_bp.get("/documents/<document_id>/published")
@check_user_auth
def download_published(document_id: str):
    try:
        tenant_id, principal_id = _identity()
        content, artifact = _service().download_published(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        document = _service().get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        return send_file(
            io.BytesIO(content),
            mimetype=str(artifact["media_type"]),
            as_attachment=True,
            download_name=f"{document_id}-v{document['version']}.{artifact['format']}",
            max_age=0,
        )
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (RuntimeError, TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)


@spreadsheet_studio_bp.post("/proposals/execute")
@check_user_auth
def execute_proposal():
    queued = current_app.extensions.get("spreadsheet_proposal_execution_service") is not None
    return _invoke(
        lambda: _proposal_execution_service().execute_proposal(
            tenant_id=_identity()[0],
            principal_id=_identity()[1],
            proposal=_body(),
        ),
        created=not queued,
        accepted=queued,
    )


@spreadsheet_studio_bp.get("/proposals/<proposal_id>/diff")
@check_user_auth
def get_proposal_diff(proposal_id: str):
    return _invoke(
        lambda: _service().get_proposal_diff(
            tenant_id=_identity()[0],
            proposal_id=proposal_id,
            principal_id=_identity()[1],
            offset=int(request.args.get("offset") or 0),
            limit=int(request.args.get("limit") or 1_000),
        )
    )


@spreadsheet_studio_bp.get("/proposal-jobs/<job_id>")
@check_user_auth
def get_proposal_job(job_id: str):
    return _invoke(
        lambda: _proposal_job_service().get_job(
            tenant_id=_identity()[0],
            principal_id=_identity()[1],
            job_id=job_id,
        )
    )


@spreadsheet_studio_bp.post("/internal/jobs/claim")
def claim_execution_job():
    if not _worker_authenticated():
        return api_response(status="error", message="spreadsheet_worker_unauthorized", code=401)
    try:
        body = _body()
        if set(body) != {"worker_id"}:
            raise ValueError("spreadsheet_worker_claim_fields_invalid")
        worker_id = str(body.get("worker_id") or "").strip()
        configured_worker = str(current_app.config.get("ANANTA_SPREADSHEET_WORKER_ID") or "spreadsheet-worker").strip()
        if worker_id != configured_worker:
            raise PermissionError("spreadsheet_worker_identity_mismatch")
        assignment = _execution_ingress_service().claim(worker_id=worker_id)
        if assignment is None:
            return Response(status=204)
        return api_response(data=assignment)
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=409)


@spreadsheet_studio_bp.get("/internal/jobs/<job_id>/artifact")
def read_execution_artifact(job_id: str):
    try:
        content, source = _execution_ingress_service().read_source_artifact(
            job_id=job_id,
            token=_bearer_token(),
        )
        return Response(
            content,
            status=200,
            content_type=str(source.get("media_type") or "application/octet-stream"),
            headers={
                "Cache-Control": "no-store",
                "X-Content-SHA256": str(source.get("sha256") or ""),
            },
        )
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=403)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=409)


@spreadsheet_studio_bp.post("/internal/jobs/<job_id>/result")
def accept_execution_result(job_id: str):
    try:
        result = _execution_ingress_service().accept_result(
            job_id=job_id,
            token=_bearer_token(),
            payload=_body(),
        )
        return api_response(data=result)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except PermissionError as exc:
        return api_response(status="error", message=str(exc), code=403)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=403)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=409)


@spreadsheet_studio_bp.get("/operations")
@check_user_auth
@admin_required
def spreadsheet_operations():
    return _invoke(
        lambda: _operations_service().snapshot(
            artifact_retention_days=int(request.args.get("artifact_retention_days") or 30)
        )
    )


@spreadsheet_studio_bp.post("/operations/reconcile")
@check_user_auth
@admin_required
def reconcile_spreadsheet_operations():
    def operation():
        body = _body()
        if set(body) != {"max_jobs", "artifact_retention_days", "delete_unreferenced_artifacts"}:
            raise ValueError("spreadsheet_operations_reconcile_fields_invalid")
        if not isinstance(body["delete_unreferenced_artifacts"], bool):
            raise ValueError("spreadsheet_operations_retention_mode_invalid")
        return _operations_service().reconcile(
            max_jobs=body["max_jobs"],
            artifact_retention_days=body["artifact_retention_days"],
            delete_unreferenced_artifacts=body["delete_unreferenced_artifacts"],
        )

    return _invoke(operation)


@spreadsheet_studio_bp.post("/feedback")
@check_user_auth
def record_feedback():
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _learning_service().record_feedback(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=_body(),
        ),
        created=True,
    )


@spreadsheet_studio_bp.get("/feedback/<event_id>/privacy-preview")
@check_user_auth
def privacy_preview(event_id: str):
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _learning_service().privacy_preview(
            tenant_id=tenant_id,
            principal_id=principal_id,
            event_id=event_id,
        )
    )


@spreadsheet_studio_bp.post("/consents")
@check_user_auth
def grant_consent():
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _learning_service().grant_consent(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=_body(),
        ),
        created=True,
    )


@spreadsheet_studio_bp.post("/consents/<consent_id>/revoke")
@check_user_auth
def revoke_consent(consent_id: str):
    body = _body()
    if set(body) != {"expected_version"}:
        return api_response(status="error", message="spreadsheet_consent_revoke_fields_invalid", code=422)
    tenant_id, principal_id = _identity()

    def operation():
        revoked = _learning_service().revoke_consent(
            tenant_id=tenant_id,
            principal_id=principal_id,
            consent_id=consent_id,
            expected_version=body["expected_version"],
        )
        impact = dict(revoked.get("impact") or {})
        if impact.get("training_jobs") or impact.get("adapters") or impact.get("dataset_digests"):
            from agent.routes.ml_intern_training_unsloth_support import get_ml_intern_training_services
            from agent.services.ml_intern_lora_runtime_composition import (
                lora_runtime_management_service_from_config,
            )

            try:
                runtime = lora_runtime_management_service_from_config(
                    dict(current_app.config.get("AGENT_CONFIG", {}) or {})
                )
            except (RuntimeError, ValueError):
                runtime = None
            impact = SpreadsheetConsentRevocationCoordinator(
                control=get_ml_intern_training_services().control,
                runtime=runtime,
            ).reconcile(tenant_id=tenant_id, impact=impact)
        return {**revoked, "impact": impact}

    return _invoke(operation)


@spreadsheet_studio_bp.post("/datasets/materialize")
@check_user_auth
def materialize_dataset():
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _learning_service().materialize_dataset(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=_body(),
        ),
        created=True,
    )


@spreadsheet_studio_bp.get("/datasets/<dataset_id>")
@check_user_auth
def get_dataset(dataset_id: str):
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _learning_service().get_dataset(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
        )
    )


@spreadsheet_studio_bp.post("/baselines")
@check_user_auth
def create_training_baseline():
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _training_admission_service().create_baseline(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=_body(),
        ),
        created=True,
    )


@spreadsheet_studio_bp.get("/baselines/<baseline_id>")
@check_user_auth
def get_training_baseline(baseline_id: str):
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _training_admission_service().get_baseline(
            tenant_id=tenant_id,
            principal_id=principal_id,
            baseline_id=baseline_id,
        )
    )


@spreadsheet_studio_bp.post("/datasets/<dataset_id>/training-admissions")
@check_user_auth
@admin_required
def create_training_admission(dataset_id: str):
    body = _body()
    if body.get("dataset_id") != dataset_id:
        return api_response(status="error", message="spreadsheet_training_dataset_binding_invalid", code=422)
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _training_admission_service().admit(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=body,
        ),
        created=True,
    )


@spreadsheet_studio_bp.get("/training-admissions/<admission_id>")
@check_user_auth
@admin_required
def get_training_admission(admission_id: str):
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _training_admission_service().get_admission(
            tenant_id=tenant_id,
            principal_id=principal_id,
            admission_id=admission_id,
        )
    )


@spreadsheet_studio_bp.post("/adapters/<adapter_id>/admissions")
@check_user_auth
@admin_required
def admit_spreadsheet_adapter(adapter_id: str):
    body = _body()
    if body.get("adapter_id") != adapter_id:
        return api_response(status="error", message="spreadsheet_adapter_binding_invalid", code=422)
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(idempotency_key) <= 191 or any(character.isspace() for character in idempotency_key):
        return api_response(status="error", message="spreadsheet_adapter_idempotency_key_invalid", code=400)
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: _adapter_admission_service().admit(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=body,
            idempotency_key=idempotency_key,
        ),
        created=True,
    )


@spreadsheet_studio_bp.post("/inference/proposals")
@check_user_auth
def infer_proposal():
    tenant_id, principal_id = _identity()
    return _invoke(
        lambda: SpreadsheetInferenceService(
            documents=_service(),
            inference=get_lora_inference_service(),
        ).propose_actions(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=_body(),
        ),
        created=True,
    )


@spreadsheet_studio_bp.post("/datasets/<dataset_id>/training")
@check_user_auth
@admin_required
def start_training(dataset_id: str):
    from agent.routes.ml_intern_training_unsloth_support import get_ml_intern_training_services

    body = _body()
    if body.get("dataset_id") != dataset_id:
        return api_response(status="error", message="spreadsheet_training_dataset_binding_invalid", code=422)
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(idempotency_key) <= 191 or any(character.isspace() for character in idempotency_key):
        return api_response(status="error", message="spreadsheet_training_idempotency_key_invalid", code=400)
    tenant_id, principal_id = _identity()

    def operation():
        services = get_ml_intern_training_services()
        return SpreadsheetMlInternBridgeService(
            learning=_learning_service(),
            catalog=services.catalog,
            split=services.split,
            repository_bridge=services.bridge,
            control=services.control,
            admissions=_training_admission_service(),
        ).start_training(
            tenant_id=tenant_id,
            principal_id=principal_id,
            payload=body,
            idempotency_key=idempotency_key,
        )

    return _invoke(operation, created=True)


__all__ = ["spreadsheet_studio_bp"]
