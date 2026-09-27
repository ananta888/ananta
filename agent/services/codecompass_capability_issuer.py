"""Hub-issued, signed CodeCompass capabilities for a requester, a task and a worker (WCRB-007).

The retrieval capability (``codecompass_retrieval_capability_service``) carries
the scope a CodeCompass call may touch, sealed only by an unkeyed digest: fine
while it never leaves the Hub. Delegated execution hands it to a worker, so it
is additionally signed here with a key only the Hub holds (HMAC-SHA256), and
bound to the task, the receiving worker (``audience``) and the operations the
requester's roles allow. A worker cannot mint, widen or re-target one; Hub
endpoints the worker calls back (e.g. graph artifacts) verify the signature.

Scope: repository id and revision come from the Hub's completed knowledge
index; paths and index ids from the requester's role constraints, or the whole
repository when unconstrained.

The Hub key is read from ``ANANTA_CODECOMPASS_CAPABILITY_KEY_FILE`` or derived
from the Hub secret (a distinct, labelled derivation, not the secret itself).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.codecompass_retrieval_capability_service import (
    bind_retrieval_capability,
    verify_retrieval_capability,
)

KEY_FILE_ENV = "ANANTA_CODECOMPASS_CAPABILITY_KEY_FILE"
_KEY_LABEL = b"ananta.codecompass.capability.v1"
_SIGNED_EXTRA = ("task_id", "audience", "allowed_operations")
DEFAULT_TTL_SECONDS = 900
_CODECOMPASS_PREFIX = "ananta.tool.codecompass."
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKIPPED_TOP_LEVEL = {"data", "node_modules", "project-workspaces", "artifacts"}


class CapabilityError(ValueError):
    """A capability that cannot be issued or must not be trusted."""


def capability_key(environ: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if environ is None else environ
    key_file = str(env.get(KEY_FILE_ENV) or "").strip()
    if key_file:
        key = Path(key_file).read_bytes().strip()
        if len(key) < 32:
            raise CapabilityError("capability_key_too_short")
        return key
    from agent.config import settings

    secret = str(getattr(settings, "secret_key", "") or "").encode("utf-8")
    if len(secret) < 16:
        raise CapabilityError("capability_key_unavailable")
    return hmac.new(secret, _KEY_LABEL, hashlib.sha256).digest()


def _signed_payload(capability: Mapping[str, Any]) -> bytes:
    body = {key: capability.get(key) for key in sorted(capability) if key != "capability_signature"}
    body["allowed_operations"] = sorted({str(item) for item in body.get("allowed_operations") or []})
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("ascii")


def sign_capability(capability: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    signed = dict(capability)
    signed["allowed_operations"] = sorted({str(item) for item in signed.get("allowed_operations") or []})
    signed["capability_signature"] = hmac.new(key, _signed_payload(signed), hashlib.sha256).hexdigest()
    return signed


def verify_signed_capability(value: Any, *, key: bytes, task_id: str | None = None, audience: str | None = None,
                             operation_id: str | None = None, now_epoch: float | None = None) -> dict[str, Any]:
    """Scope, expiry, digest and signature; plus the task, worker and operation it must be bound to."""
    if not isinstance(value, Mapping):
        raise CapabilityError("capability_required")
    try:
        verify_retrieval_capability(value, now_epoch=now_epoch)
    except ValueError as error:
        raise CapabilityError(str(error)) from None
    supplied = str(value.get("capability_signature") or "")
    expected = hmac.new(key, _signed_payload(value), hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise CapabilityError("capability_signature_invalid")
    if task_id is not None and str(value.get("task_id") or "") != task_id:
        raise CapabilityError("capability_task_mismatch")
    if audience is not None and str(value.get("audience") or "") != audience:
        raise CapabilityError("capability_audience_mismatch")
    if operation_id is not None and operation_id not in set(value.get("allowed_operations") or []):
        raise CapabilityError("capability_operation_not_allowed")
    return dict(value)


# --- scope ---------------------------------------------------------------------------------------


def repository_top_level(root: Path = _REPO_ROOT) -> list[str]:
    """Every visible top-level entry of the repository: the unconstrained path scope."""
    try:
        return sorted(entry.name for entry in root.iterdir()
                      if not entry.name.startswith(".") and entry.name not in _SKIPPED_TOP_LEVEL)
    except OSError:
        return []


def resolve_index_binding() -> tuple[str, str, str] | None:
    """``(index_id, repository_id, revision)`` of the Hub's consumable CodeCompass graph index."""
    from agent.services.repository_registry import get_repository_registry
    from agent.services.tools.codecompass_tools import _resolve_graph_store

    _store, index_id = _resolve_graph_store({})
    if not index_id:
        return None
    index = get_repository_registry().knowledge_index_repo.get_by_id(index_id)
    metadata = dict(getattr(index, "index_metadata", None) or {})
    graph = dict(metadata.get("graph_artifacts") or {})
    revision = str(graph.get("graph_revision") or metadata.get("codecompass_snapshot_revision") or "").strip()
    repository_id = str(getattr(index, "source_path", None) or metadata.get("source_id") or _REPO_ROOT.name).strip()
    if not revision or not repository_id:
        return None
    return index_id, repository_id, revision


def allowed_codecompass_operations(grants: Any) -> list[str]:
    """CodeCompass tool operations the grants allow; every one of them when unrestricted (``None``)."""
    from agent.services.operation_registry_service import get_operation_registry_service

    registry = get_operation_registry_service()
    operations = [descriptor.operation_id for descriptor in registry.list_descriptors()
                  if descriptor.operation_id.startswith(_CODECOMPASS_PREFIX)]
    if grants is None or not getattr(grants, "roles", None):
        return sorted(operations)
    return sorted(operation for operation in operations
                  if grants.allows(operation, registry.groups_for(operation))[0])


def issue_capability(*, subject_id: str, tenant_id: str | None, task_id: str | None, audience: str | None,
                     grants: Any = None, workspace_id: str | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                     key: bytes | None = None, binding: tuple[str, str, str] | None = None,
                     top_level: list[str] | None = None, now_epoch: float | None = None) -> dict[str, Any]:
    """A signed capability for ``subject_id``'s grants, bound to ``task_id`` and ``audience``."""
    binding = binding or resolve_index_binding()
    if binding is None:
        raise CapabilityError("capability_index_unavailable")
    index_id, repository_id, revision = binding
    constraints = dict(getattr(grants, "constraints", None) or {})
    paths = constraints.get("paths")
    allowed_paths = sorted(paths) if paths is not None else (top_level if top_level is not None
                                                              else repository_top_level())
    index_ids = constraints.get("index_ids")
    if index_ids is not None and index_id not in index_ids:
        raise CapabilityError("capability_index_not_granted")
    if not allowed_paths:
        raise CapabilityError("capability_paths_empty")
    tenant = str(tenant_id or "local").strip() or "local"
    sealed = bind_retrieval_capability(
        {"workspace_id": workspace_id or f"tenant:{tenant}", "repository_id": repository_id,
         "source_scope": "repo_path", "revision": revision, "allowed_paths": allowed_paths,
         "allowed_index_ids": [index_id], "allowed_signals": ["exact", "graph", "vector"]},
        subject_id=subject_id, tenant_id=tenant, ttl_seconds=ttl_seconds,
        now_epoch=time.time() if now_epoch is None else now_epoch,
    )
    sealed.update(task_id=str(task_id or ""), audience=str(audience or ""),
                  allowed_operations=allowed_codecompass_operations(grants))
    return sign_capability(sealed, key or capability_key())


class HubRetrievalCapabilityResolver:
    """``codecompass_retrieval_capability_resolver`` for in-Hub calls (MCP, companion, gateway)."""

    def resolve(self, *, principal: Any, requested_scope: Mapping[str, Any]) -> Mapping[str, Any] | None:
        from agent.services.access_role_admin_service import get_access_role_admin_service
        from agent.services.access_roles import IdentityClaims

        subject = str(getattr(principal, "subject_id", "") or "").strip()
        if not subject:
            return None
        grants = get_access_role_admin_service().grants_for(IdentityClaims(
            username=subject, tenant_id=getattr(principal, "tenant_id", None),
            project_id=getattr(principal, "project_id", None)))
        try:
            capability = issue_capability(subject_id=subject, tenant_id=getattr(principal, "tenant_id", None),
                                          task_id=str(requested_scope.get("task_id") or ""),
                                          audience=str(requested_scope.get("audience") or "hub"), grants=grants)
        except CapabilityError:
            return None
        return capability
