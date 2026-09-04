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


def _extension(name: str):
    value = current_app.extensions.get(name)
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


@collaboration_workspaces_bp.put("/<workspace_id>/rooms/<room_id>/access")
@check_user_auth
def put_room_access(workspace_id: str, room_id: str):
    def operation():
        body = _body()
        actors = body.get("actor_binding_ids")
        if not isinstance(actors, list):
            raise ValueError("collaboration_room_actor_bindings_invalid")
        expected_revision = body.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("collaboration_room_access_revision_invalid")
        return _service().put_room_access(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            room_id=room_id,
            access_mode=str(body.get("access_mode") or ""),
            actor_binding_ids=actors,
            expected_revision=expected_revision,
        )

    return _invoke(operation)


@collaboration_workspaces_bp.put("/<workspace_id>/rooms/<room_id>/lifecycle")
@check_user_auth
def transition_room(workspace_id: str, room_id: str):
    def operation():
        body = _body()
        expected_revision = body.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("collaboration_room_lifecycle_revision_invalid")
        return _service().transition_room(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            room_id=room_id,
            target_state=str(body.get("state") or ""),
            expected_revision=expected_revision,
        )

    return _invoke(operation)


@collaboration_workspaces_bp.put("/<workspace_id>/rooms/<room_id>/binding")
@check_user_auth
def put_room_binding(workspace_id: str, room_id: str):
    def operation():
        body = _body()
        expected_revision = body.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("collaboration_room_binding_revision_invalid")
        return _extension("collaboration_binding_service").bind_room(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            room_id=room_id,
            principal_actor_id=_identity()[2],
            binding=body.get("binding") or {},
            expected_revision=expected_revision,
        )

    return _invoke(operation)


@collaboration_workspaces_bp.post("/<workspace_id>/branch-rooms")
@check_user_auth
def create_branch_room(workspace_id: str):
    def operation():
        body = _body()
        return _extension("collaboration_binding_service").create_branch_room(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            title=body.get("title"),
            binding=body.get("binding") or {},
        )

    return _invoke(operation, created=True)


@collaboration_workspaces_bp.get("/<workspace_id>/threads/<thread_id>")
@check_user_auth
def get_thread(workspace_id: str, thread_id: str):
    return _invoke(
        lambda: _service().thread(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            thread_id=thread_id,
        )
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


@collaboration_workspaces_bp.put("/<workspace_id>/actors/<actor_binding_id>/external-identities/<provider>")
@check_user_auth
def put_external_identity(workspace_id: str, actor_binding_id: str, provider: str):
    def operation():
        body = _body()
        expected_revision = body.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("collaboration_identity_link_revision_invalid")
        return _service().put_external_identity(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            actor_binding_id=actor_binding_id,
            provider=provider,
            external_subject=body.get("external_subject"),
            key_fingerprint=body.get("key_fingerprint"),
            status=body.get("status"),
            expected_revision=expected_revision,
        )

    return _invoke(operation)


@collaboration_workspaces_bp.post("/<workspace_id>/resource-offers")
@check_user_auth
def publish_resource_offer(workspace_id: str):
    def operation():
        body = _body()
        if body.get("workspace_id") != workspace_id:
            raise PermissionError("collaboration_resource_offer_workspace_mismatch")
        return _extension("collaboration_agent_control_service").publish_offer(
            tenant_id=_identity()[0], principal_actor_id=_identity()[2], offer=body
        )

    return _invoke(operation, created=True)


@collaboration_workspaces_bp.post("/<workspace_id>/agent-intents")
@check_user_auth
def propose_agent_intent(workspace_id: str):
    def operation():
        body = _body()
        if body.get("workspace_id") != workspace_id:
            raise PermissionError("collaboration_agent_intent_workspace_mismatch")
        return _extension("collaboration_agent_control_service").propose_intent(
            tenant_id=_identity()[0], principal_actor_id=_identity()[2], intent=body
        )

    return _invoke(operation, created=True)


@collaboration_workspaces_bp.post("/<workspace_id>/command-decisions")
@check_user_auth
def decide_command(workspace_id: str):
    def operation():
        body = _body()
        if body.get("workspace_id") != workspace_id:
            raise PermissionError("collaboration_command_workspace_mismatch")
        return _extension("collaboration_command_service").decide(
            tenant_id=_identity()[0], principal_actor_id=_identity()[2], request=body
        )

    return _invoke(operation, created=True)


@collaboration_workspaces_bp.post("/<workspace_id>/tasks/<task_id>/cancel")
@check_user_auth
def cancel_task(workspace_id: str, task_id: str):
    tenant_id, _principal, actor_id = _identity()
    return _invoke(
        lambda: _extension("collaboration_agent_control_service").cancel_task(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_actor_id=actor_id,
            task_id=task_id,
        )
    )


@collaboration_workspaces_bp.post("/<workspace_id>/events")
@check_user_auth
def append_event(workspace_id: str):
    connection_id = f"connection-{hashlib.sha256(str(request.remote_addr or 'unknown').encode()).hexdigest()[:24]}"
    return _invoke(
        lambda: _service().append_event(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            event=_body(),
            connection_id=connection_id,
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


@collaboration_workspaces_bp.get("/<workspace_id>/events/query")
@check_user_auth
def query_events(workspace_id: str):
    filters = {
        key: request.args[key]
        for key in ("room_id", "thread_id", "actor_binding_id", "event_type", "causation_id")
        if key in request.args
    }
    for key in ("occurred_after", "occurred_before"):
        if key in request.args:
            filters[key] = float(request.args[key])
    return _invoke(
        lambda: _service().query_events(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            filters=filters,
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


@collaboration_workspaces_bp.post("/<workspace_id>/search/rebuild")
@check_user_auth
def rebuild_search(workspace_id: str):
    tenant_id, _principal, actor_id = _identity()
    return _invoke(
        lambda: _extension("collaboration_search_service").rebuild_for_actor(
            tenant_id,
            workspace_id,
            actor_id,
            mode=str((_body().get("mode") or "full")),
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>/indexed-search")
@check_user_auth
def indexed_search(workspace_id: str):
    return _invoke(
        lambda: _extension("collaboration_search_service").query(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            principal_actor_id=_identity()[2],
            query=request.args.get("q") or "",
            limit=int(request.args.get("limit") or 20),
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>/rooms/<room_id>/memory")
@check_user_auth
def room_memory(workspace_id: str, room_id: str):
    return _invoke(
        lambda: _extension("collaboration_search_service").room_memory(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            room_id=room_id,
            principal_actor_id=_identity()[2],
            maximum_events=int(request.args.get("limit") or 20),
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>/rooms/<room_id>/presence")
@check_user_auth
def room_presence(workspace_id: str, room_id: str):
    return _invoke(
        lambda: _service().room_presence(
            tenant_id=_identity()[0],
            workspace_id=workspace_id,
            room_id=room_id,
            principal_actor_id=_identity()[2],
        )
    )


@collaboration_workspaces_bp.get("/<workspace_id>/flow-projection")
@check_user_auth
def flow_projection(workspace_id: str):
    tenant_id, _principal, actor_id = _identity()

    def operation():
        _service().get_workspace(tenant_id=tenant_id, workspace_id=workspace_id, principal_actor_id=actor_id)
        return _extension("collaboration_flow_projection_service").rebuild(
            tenant_id, workspace_id, principal_actor_id=actor_id
        )

    return _invoke(operation)


@collaboration_workspaces_bp.get("/<workspace_id>/operations")
@check_user_auth
def operations(workspace_id: str):
    tenant_id, _principal, actor_id = _identity()

    def operation():
        _service().get_workspace(tenant_id=tenant_id, workspace_id=workspace_id, principal_actor_id=actor_id)
        return _extension("collaboration_observability_service").snapshot(tenant_id, workspace_id)

    return _invoke(operation)


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


@collaboration_workspaces_bp.get("/legacy-migration/<session_id>/plan")
@check_user_auth
def legacy_migration_plan(session_id: str):
    tenant_id, principal_id, actor_id = _identity()
    return _invoke(
        lambda: _extension("collaboration_legacy_migration_service").plan(
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_actor_id=actor_id,
            session_id=session_id,
        )
    )


@collaboration_workspaces_bp.post("/legacy-migration/<session_id>/execute")
@check_user_auth
def execute_legacy_migration(session_id: str):
    tenant_id, principal_id, actor_id = _identity()

    def operation():
        body = _body()
        owner = {
            "schema": "ananta.collaboration-actor-binding.v1",
            "actor_binding_id": actor_id,
            "actor_kind": "human",
            "authority_kind": "oidc",
            "authority_subject": principal_id,
            "display_name": str(body.get("display_name") or principal_id),
            "capabilities": [],
        }
        return _extension("collaboration_legacy_migration_service").execute(
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_actor_id=actor_id,
            session_id=session_id,
            expected_source_revision=str(body.get("expected_source_revision") or ""),
            owner=owner,
        )

    return _invoke(operation, created=True)


__all__ = ["collaboration_workspaces_bp"]
