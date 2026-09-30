"""Catalog, grant administration and index-access facade of the Source Control API runtime.

Maps principals onto the grant admin, catalog and prepare-index-access
services and attaches the server-side capability descriptors.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping

from agent.services.source_control_grant_admin import (
    GrantAdminActor,
    GrantCreateRequest,
    GrantRevokeRequest,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    runtime_principal,
    wire,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class SourceControlGrantCatalogApi:
    """Scoped catalog listings, grant presets/grants and index-access preparation."""

    def __init__(
        self,
        *,
        catalogs: object | None,
        grants: object | None,
        index_access: object | None,
    ) -> None:
        self._catalogs = catalogs
        self._grants = grants
        self._index_access = index_access

    def list_source_control_catalog(
        self,
        *,
        principal: object,
        catalog: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        if project_id != actor.project_id:
            raise SourceControlApiRuntimeError(
                "source_control_project_scope_mismatch",
                status_code=403,
            )
        service = self._catalog_service()
        if catalog == "workspaces":
            return dict(
                service.list_workspaces(
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    actor_id=actor.subject_id,
                    roles=actor.roles,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        if catalog == "registered_remotes":
            return dict(
                service.list_registered_remotes(
                    tenant_id=actor.tenant_id,
                    project_id=actor.project_id,
                    actor_id=actor.subject_id,
                    roles=actor.roles,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        if catalog == "index_profiles":
            return dict(
                service.list_index_profiles(
                    project_id=actor.project_id,
                    cursor=cursor,
                    limit=limit,
                    filters=filters,
                )
            )
        raise SourceControlApiRuntimeError("source_control_catalog_invalid")

    def list_grant_presets(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        _validate_grant_query(cursor=cursor, filters=filters)
        q = filters.get("q", "").casefold()
        operation = filters.get("operation")
        transformation = filters.get("transformation")
        if operation is not None:
            try:
                GrantOperation(operation)
            except ValueError as exc:
                raise SourceControlApiRuntimeError(
                    "grant_operation_filter_invalid"
                ) from exc
        if transformation is not None:
            try:
                GrantTransformation(transformation)
            except ValueError as exc:
                raise SourceControlApiRuntimeError(
                    "grant_transformation_filter_invalid"
                ) from exc
        presets = [
            preset
            for preset in self._grant_service().list_presets(
                actor=_grant_actor(actor)
            )
            if (
                not q
                or q
                in " ".join(
                    (
                        preset.preset_id,
                        preset.label,
                        preset.description,
                        preset.purpose,
                    )
                ).casefold()
            )
            and (
                operation is None
                or preset.operation.value == operation
            )
            and (
                transformation is None
                or preset.transformation.value == transformation
            )
        ]
        after = _decode_grant_preset_cursor(cursor)
        start = 0
        if after is not None:
            positions = {
                preset.preset_id: index
                for index, preset in enumerate(presets)
            }
            if after not in positions:
                raise SourceControlApiRuntimeError(
                    "grant_preset_cursor_invalid"
                )
            start = positions[after] + 1
        visible = presets[start : start + limit]
        has_more = start + limit < len(presets)
        return {
            "items": [wire(preset) for preset in visible],
            "next_cursor": (
                _encode_grant_preset_cursor(visible[-1].preset_id)
                if has_more and visible
                else None
            ),
            "capabilities": _grant_preset_capabilities(actor.project_id),
        }

    def list_grants(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        _validate_grant_query(cursor=cursor, filters=filters)
        page = self._grant_service().list_grants(
            actor=_grant_actor(actor),
            cursor=cursor,
            limit=limit,
            state=filters.get("state"),
            source_revision_id=filters.get("source_revision_id"),
            destination_id=filters.get("destination_id"),
        )
        result = dict(wire(page))
        result["capabilities"] = _grant_capabilities(actor.project_id)
        return result

    def create_grant(
        self,
        *,
        principal: object,
        project_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        grant = self._grant_service().create_grant(
            actor=_grant_actor(actor),
            request=GrantCreateRequest.from_mapping(payload),
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        return {
            "grant": wire(grant),
            "capabilities": _grant_capabilities(actor.project_id),
        }

    def revoke_grant(
        self,
        *,
        principal: object,
        project_id: str,
        grant_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        grant = self._grant_service().revoke_grant(
            actor=_grant_actor(actor),
            grant_id=grant_id,
            request=GrantRevokeRequest.from_mapping(payload),
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        return {
            "grant": wire(grant),
            "capabilities": _grant_capabilities(actor.project_id),
        }

    def prepare_index_access_options(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        return dict(
            self._index_access_service().options(
                actor=actor,
                connection_id=connection_id,
            )
        )

    def prepare_index_access(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        _require_project_scope(actor=actor, project_id=project_id)
        return dict(
            self._index_access_service().prepare(
                actor=actor,
                connection_id=connection_id,
                payload=payload,
                if_match=if_match,
                idempotency_key=idempotency_key,
            )
        )

    def _catalog_service(self):
        if self._catalogs is None:
            raise SourceControlApiRuntimeError(
                "source_control_catalog_unavailable", status_code=503
            )
        return self._catalogs

    def _grant_service(self):
        if self._grants is None:
            raise SourceControlApiRuntimeError(
                "source_control_grant_admin_unavailable", status_code=503
            )
        return self._grants

    def _index_access_service(self):
        if self._index_access is None:
            raise SourceControlApiRuntimeError(
                "source_control_index_access_unavailable", status_code=503
            )
        return self._index_access


def _grant_actor(value: object) -> GrantAdminActor:
    actor = runtime_principal(value)
    return GrantAdminActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


def _require_project_scope(
    *, actor: SourceControlPrincipal, project_id: str
) -> None:
    if project_id != actor.project_id:
        raise SourceControlApiRuntimeError(
            "source_control_project_scope_mismatch",
            status_code=403,
        )


def _validate_grant_query(
    *, cursor: str | None, filters: Mapping[str, str]
) -> None:
    if cursor is not None and len(cursor) > 512:
        raise SourceControlApiRuntimeError("grant_cursor_invalid")
    for key, value in filters.items():
        maximum = 128 if key == "q" else 255
        if len(value) > maximum:
            raise SourceControlApiRuntimeError(
                f"grant_{key}_filter_invalid"
            )


def _encode_grant_preset_cursor(preset_id: str) -> str:
    return (
        base64.urlsafe_b64encode(preset_id.encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )


def _decode_grant_preset_cursor(cursor: str | None) -> str | None:
    if cursor in (None, ""):
        return None
    try:
        raw = str(cursor)
        raw += "=" * (-len(raw) % 4)
        preset_id = base64.urlsafe_b64decode(raw).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlApiRuntimeError(
            "grant_preset_cursor_invalid"
        ) from exc
    if not preset_id or len(preset_id) > 255:
        raise SourceControlApiRuntimeError(
            "grant_preset_cursor_invalid"
        )
    return preset_id


def _grant_preset_capabilities(project_id: str) -> Mapping[str, object]:
    return {
        "read_only": True,
        "selection_mode": "server_ids_only",
        "project_id": project_id,
        "browser_ids_accepted": False,
        "create_grant": {
            "supported": True,
            "requires_if_match": True,
            "requires_idempotency_key": True,
        },
    }


def _grant_capabilities(project_id: str) -> Mapping[str, object]:
    return {
        "read_only": False,
        "selection_mode": "server_ids_only",
        "project_id": project_id,
        "browser_ids_accepted": False,
        "create_supported": True,
        "revoke_supported": True,
        "requires_if_match": True,
        "requires_idempotency_key": True,
        "destination_resolution": "server",
        "policy_resolution": "server",
    }


