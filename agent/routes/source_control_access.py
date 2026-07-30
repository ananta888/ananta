"""Thin Flask composition for the common Hub source-control policy port."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Protocol

from flask import current_app, g, request

from agent.auth import get_authenticated_source_control_principal
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceControlAccessPolicy,
    SourceControlAccessPolicyError,
    SourceControlAction,
    SourceObjectBinding,
)


class SourceScopeProjectionPort(Protocol):
    """Optional adapter over canonical persistence/projection bindings."""

    def resolve_binding(
        self,
        *,
        resource_kind: str,
        object_id: str,
        principal: HubSourcePrincipal,
    ) -> SourceObjectBinding | None: ...


class SourceControlRouteDenyAuditPort(Protocol):
    def record_denial(self, event: Mapping[str, object]) -> None: ...


_POLICY = SourceControlAccessPolicy()
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SCOPE_METADATA_KEYS = (
    "source_control_scope",
    "source_control_binding",
    "scope_binding",
)
_CONTAINER_KEYS = (
    "collection_metadata",
    "index_metadata",
    "policy_json",
    "run_metadata",
    "source_metadata",
    "task_metadata",
)


def _as_mapping(resource: Any) -> dict[str, Any]:
    if isinstance(resource, Mapping):
        return dict(resource)
    if hasattr(resource, "model_dump"):
        payload = resource.model_dump()
        return dict(payload) if isinstance(payload, Mapping) else {}
    if hasattr(resource, "dict"):
        payload = resource.dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    if hasattr(resource, "__dict__"):
        return {
            key: value
            for key, value in vars(resource).items()
            if not key.startswith("_")
        }
    return {}


def _scope_candidates(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    candidates: list[Mapping[str, Any]] = [payload]
    for key in _SCOPE_METADATA_KEYS:
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    for container_key in _CONTAINER_KEYS:
        container = payload.get(container_key)
        if not isinstance(container, Mapping):
            continue
        candidates.append(container)
        for key in _SCOPE_METADATA_KEYS:
            value = container.get(key)
            if isinstance(value, Mapping):
                candidates.append(value)
    return tuple(candidates)


def _direct_binding(
    *,
    resource: Any,
    object_id: str,
) -> SourceObjectBinding:
    if resource is None:
        return SourceObjectBinding(
            object_id=object_id,
            tenant_id=None,
            project_id=None,
            exists=False,
        )
    payload = _as_mapping(resource)
    tenant_id = ""
    project_id = ""
    owner_id = ""
    visible_subject_ids: frozenset[str] = frozenset()
    binding_source = "legacy"
    for candidate in _scope_candidates(payload):
        candidate_tenant = str(candidate.get("tenant_id") or "").strip()
        candidate_project = str(candidate.get("project_id") or "").strip()
        if candidate_tenant and candidate_project:
            tenant_id = candidate_tenant
            project_id = candidate_project
            owner_id = str(
                candidate.get("owner_id")
                or candidate.get("created_by")
                or payload.get("owner_id")
                or payload.get("created_by")
                or ""
            ).strip()
            visible = candidate.get("visible_subject_ids") or ()
            if isinstance(visible, (list, tuple, set, frozenset)):
                visible_subject_ids = frozenset(
                    str(item).strip()
                    for item in visible
                    if str(item).strip()
                )
            binding_source = "direct"
            break
    return SourceObjectBinding(
        object_id=object_id,
        tenant_id=tenant_id or None,
        project_id=project_id or None,
        owner_id=owner_id or None,
        visible_subject_ids=visible_subject_ids,
        exists=True,
        binding_source=binding_source,
    )


def project_resource_binding(
    *,
    resource: Any,
    object_id: str,
    resource_kind: str,
    principal: HubSourcePrincipal,
) -> SourceObjectBinding:
    binding = _direct_binding(resource=resource, object_id=object_id)
    if (
        binding.exists
        and not binding.is_scoped
        and principal.tenant_id
        and principal.project_id
    ):
        projection = current_app.extensions.get(
            "source_control_scope_projection"
        )
        if projection is not None:
            try:
                projected = projection.resolve_binding(
                    resource_kind=resource_kind,
                    object_id=object_id,
                    principal=principal,
                )
            except Exception:
                projected = None
            if isinstance(projected, SourceObjectBinding):
                return projected
    return binding


def authorize_route_request(
    *,
    action: SourceControlAction,
    resource_kind: str,
    resource: Any = None,
    object_id: str = "",
    collection: bool = False,
):
    try:
        principal = get_authenticated_source_control_principal()
    except SourceControlAccessPolicyError as exc:
        record_source_control_route_denial(
            principal=None,
            action=action,
            resource_kind=resource_kind,
            object_id=object_id,
            status_code=403,
            reason_code=exc.reason_code,
        )
        return api_response(
            status="error",
            message="forbidden",
            data={"reason_code": exc.reason_code},
            code=403,
        )
    g.source_control_principal = principal
    binding = None
    if not collection:
        binding = project_resource_binding(
            resource=resource,
            object_id=object_id,
            resource_kind=resource_kind,
            principal=principal,
        )
        g.source_control_binding = binding
    decision = _POLICY.authorize(
        principal=principal,
        action=action,
        binding=binding,
    )
    if decision.legacy_admin_access:
        log_audit(
            "source_control_admin_legacy_access",
            {
                "action": action.value,
                "resource_kind": resource_kind,
                "object_id": object_id,
                "path": request.path,
            },
        )
    if decision.allowed:
        return None
    record_source_control_route_denial(
        principal=principal,
        action=action,
        resource_kind=resource_kind,
        object_id=object_id,
        status_code=decision.status_code,
        reason_code=decision.reason_code,
    )
    return api_response(
        status="error",
        message=(
            "not_found" if decision.status_code == 404 else "forbidden"
        ),
        data={"reason_code": decision.reason_code},
        code=decision.status_code,
    )


def record_source_control_route_denial(
    *,
    principal: HubSourcePrincipal | None,
    action: SourceControlAction | str,
    resource_kind: str,
    object_id: str,
    status_code: int,
    reason_code: str,
) -> None:
    """Emit a bounded, content-free denial without foreign raw identifiers."""

    raw_trace = str(
        request.headers.get("X-Request-ID")
        or getattr(g, "request_id", "")
        or ""
    ).strip()
    trace_id = raw_trace if _TRACE_ID.fullmatch(raw_trace) else "unavailable"
    route = (
        str(request.url_rule.rule)
        if request.url_rule is not None
        else "unmatched"
    )
    reference = (
        hashlib.sha256(str(object_id).encode("utf-8")).hexdigest()[:16]
        if object_id
        else "collection"
    )
    event: dict[str, object] = {
        "actor_id": (
            str(principal.subject_id)[:191]
            if principal is not None
            else "anonymous"
        ),
        "tenant_id": (
            str(principal.tenant_id or "")[:191]
            if principal is not None
            else ""
        ),
        "project_id": (
            str(principal.project_id or "")[:191]
            if principal is not None
            else ""
        ),
        "resource_kind": str(resource_kind or "unknown")[:64],
        "resource_reference": reference,
        "action": str(
            action.value if isinstance(action, SourceControlAction) else action
        )[:32],
        "reason_code": str(reason_code or "access_denied")[:128],
        "status_code": int(status_code),
        "route": route[:256],
        "trace_id": trace_id,
        "outcome": "deny",
    }
    audit = current_app.extensions.get("source_control_route_deny_audit")
    record = getattr(audit, "record_denial", None)
    if callable(record):
        try:
            record(event)
            return
        except Exception:
            pass
    log_audit("source_control_access_denied", event)


def filter_visible_resources(
    resources: Iterable[Any],
    *,
    resource_kind: str,
    object_id: Callable[[Any], str],
) -> list[Any]:
    principal = getattr(g, "source_control_principal", None)
    if not isinstance(principal, HubSourcePrincipal):
        principal = get_authenticated_source_control_principal()
    visible: list[Any] = []
    for resource in resources:
        identifier = str(object_id(resource) or "").strip()
        binding = project_resource_binding(
            resource=resource,
            object_id=identifier,
            resource_kind=resource_kind,
            principal=principal,
        )
        if _POLICY.can_view(principal=principal, binding=binding):
            visible.append(resource)
    return visible


__all__ = [
    "SourceControlRouteDenyAuditPort",
    "SourceScopeProjectionPort",
    "authorize_route_request",
    "filter_visible_resources",
    "project_resource_binding",
    "record_source_control_route_denial",
]
