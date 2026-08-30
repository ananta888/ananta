"""Authenticated Hub API for native collaboration workspaces."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.errors import api_response
from agent.services.collaboration_workspace_policy import CollaborationPolicyDenied
from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import CollaborationContractError

collaboration_workspaces_bp = Blueprint(
    "collaboration_workspaces", __name__, url_prefix="/api/collaboration/workspaces"
)


def _service():
    value = current_app.extensions.get("collaboration_workspace_service")
    if value is None:
        raise RuntimeError("collaboration_workspace_unavailable")
    return value


def _identity() -> tuple[str, str, str]:
    identity = dict(get_request_auth_context() or {})
    principal = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or principal).strip()
    if not principal or not tenant:
        raise PermissionError("collaboration_principal_invalid")
    actor_id = f"human-{hashlib.sha256(principal.encode()).hexdigest()[:32]}"
    return tenant, principal, actor_id


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("collaboration_payload_invalid")
    return value


def _invoke(operation: Callable[[], Any], *, created: bool = False):
    try:
        return api_response(data=operation(), code=201 if created else 200)
    except CollaborationStoreConflict as exc:
        return api_response(status="error", message=str(exc), code=409)
    except (CollaborationPolicyDenied, PermissionError) as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (CollaborationContractError, TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@collaboration_workspaces_bp.get("/capabilities")
@check_user_auth
def capabilities():
    def operation():
        status = current_app.extensions.get("collaboration_workspace_wiring_status")
        bridge = current_app.extensions.get("collaboration_bridge")
        available = bool(status and status.ready)
        return {
            "schema": "ananta.collaboration-capability.v1",
            "native_core": "available" if available else "disabled",
            "available": available,
            "reason_code": None if available else getattr(status, "reason_code", "collaboration_workspace_disabled"),
            "bridge": bridge.capabilities if bridge is not None else DisabledBridgeProjection.VALUE,
            "multi_participant_live_ready": False,
            "human_intervention_required": False,
        }

    return _invoke(operation)


class DisabledBridgeProjection:
    VALUE = {
        "schema": "ananta.collaboration-bridge-capability.v1",
        "state": "disabled",
        "mapping_versions": [],
        "supports_outbound": False,
        "supports_inbound_proposals": False,
        "supports_command_intents": False,
        "native_core_available": True,
    }


@collaboration_workspaces_bp.post("")
@check_user_auth
def create_workspace():
    def operation():
        tenant, principal, actor_id = _identity()
        body = _body()
        owner = {
            "schema": "ananta.collaboration-actor-binding.v1",
            "actor_binding_id": actor_id,
            "actor_kind": "human",
            "authority_kind": "oidc",
            "authority_subject": principal,
            "display_name": str(body.get("display_name") or principal),
            "capabilities": [],
        }
        return _service().create_workspace(
            tenant_id=tenant,
            principal_id=principal,
            title=body.get("title"),
            owner=owner,
            workspace_id=body.get("workspace_id"),
        )

    return _invoke(operation, created=True)


@collaboration_workspaces_bp.get("")
@check_user_auth
def list_workspaces():
    return _invoke(
        lambda: _service().list_workspaces(
            tenant_id=_identity()[0],
            principal_actor_id=_identity()[2],
            limit=int(request.args.get("limit") or 100),
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>")
@check_user_auth
def get_workspace(workspace_id: str):
    return _invoke(
        lambda: _service().get_workspace(
            tenant_id=_identity()[0], workspace_id=workspace_id, principal_actor_id=_identity()[2]
        )
    )


@collaboration_workspaces_bp.post("/<workspace_id>/rooms")
@check_user_auth
def create_room(workspace_id: str):
    return _invoke(
        lambda: _service().create_room(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            room=_body(),
        ),
        created=True,
    )


@collaboration_workspaces_bp.put("/<workspace_id>/memberships")
@check_user_auth
def put_membership(workspace_id: str):
    def operation():
        body = _body()
        return _service().put_membership(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            actor=body.get("actor") or {},
            role=body.get("role"),
            status=body.get("status"),
            expected_revision=body.get("expected_revision"),
        )

    return _invoke(operation)


@collaboration_workspaces_bp.post("/<workspace_id>/events")
@check_user_auth
def append_event(workspace_id: str):
    return _invoke(
        lambda: _service().append_event(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            event=_body(),
        ),
        created=True,
    )


@collaboration_workspaces_bp.get("/<workspace_id>/timeline")
@check_user_auth
def timeline(workspace_id: str):
    return _invoke(
        lambda: _service().timeline(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            room_id=request.args.get("room_id"),
            after=int(request.args.get("after") or 0),
            limit=int(request.args.get("limit") or 100),
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>/search")
@check_user_auth
def search(workspace_id: str):
    return _invoke(
        lambda: _service().search(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            query=str(request.args.get("q") or ""),
            limit=int(request.args.get("limit") or 20),
        )
    )


@collaboration_workspaces_bp.put("/<workspace_id>/rooms/<room_id>/cursor")
@check_user_auth
def acknowledge(workspace_id: str, room_id: str):
    return _invoke(
        lambda: _service().acknowledge(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            room_id=room_id,
            sequence=_body().get("sequence"),
        )
    )


@collaboration_workspaces_bp.put("/<workspace_id>/presence")
@check_user_auth
def presence(workspace_id: str):
    def operation():
        body = _body()
        return _service().renew_presence(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            lease_id=body.get("lease_id"),
            ttl_seconds=body.get("ttl_seconds"),
            epoch=body.get("epoch"),
        )

    return _invoke(operation)


@collaboration_workspaces_bp.post("/legacy-migration/dry-run")
@check_user_auth
def legacy_migration():
    return _invoke(
        lambda: _service().legacy_migration_plan(
            tenant_id=_identity()[0],
            principal_actor_id=_identity()[1],
            share_session=_body().get("share_session") or {},
        )
    )


__all__ = ["collaboration_workspaces_bp"]
