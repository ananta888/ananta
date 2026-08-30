"""Authenticated Hub API for the bounded spreadsheet studio slice."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.errors import api_response
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import SpreadsheetContractError

spreadsheet_studio_bp = Blueprint("spreadsheet_studio", __name__, url_prefix="/api/spreadsheet-studio")


def _service():
    value = current_app.extensions.get("spreadsheet_studio_service")
    if value is None:
        raise RuntimeError("spreadsheet_studio_unavailable")
    return value


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


def _invoke(operation: Callable[[], Any], *, created: bool = False):
    try:
        return api_response(data=operation(), code=201 if created else 200)
    except SpreadsheetStoreConflict as exc:
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


@spreadsheet_studio_bp.post("/proposals/execute")
@check_user_auth
def execute_proposal():
    return _invoke(
        lambda: _service().execute_proposal(tenant_id=_identity()[0], principal_id=_identity()[1], proposal=_body()),
        created=True,
    )


__all__ = ["spreadsheet_studio_bp"]
