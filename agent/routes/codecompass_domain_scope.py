"""CCRDS-016: REST endpoints for runtime domain scope.

GET  /api/codecompass/domains                 — stable sorted domain list
POST /api/codecompass/domain-scope/preview    — resolve selected_domain_ids
                                                to allowed paths/warnings

A preview never grants anything (CCRDS-DD-001): it only shows what an
explicit scope *would* allow. No absolute host paths leave the API.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.codecompass.domain_scope import DomainScope
from agent.codecompass.domain_scope_resolver import DomainScopeResolver
from agent.config import settings

codecompass_domain_scope_bp = Blueprint("codecompass_domain_scope", __name__)


def _repo_root() -> Path:
    return Path(os.environ.get("ANANTA_REPO_ROOT") or Path(__file__).resolve().parents[2])


def _resolver() -> DomainScopeResolver:
    return DomainScopeResolver(
        repo_root=_repo_root(),
        artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
        descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
    )


@codecompass_domain_scope_bp.route("/api/codecompass/domains", methods=["GET"])
@check_auth
def list_codecompass_domains():
    listing = _resolver().list_domains()
    listing["scope_enabled"] = bool(getattr(settings, "codecompass_domain_scope_enabled", False))
    return api_response(listing)


@codecompass_domain_scope_bp.route("/api/codecompass/domain-scope/preview", methods=["POST"])
@check_auth
def preview_codecompass_domain_scope():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return api_response(status="error", message="expected JSON object", code=400)
    raw_ids = body.get("selected_domain_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return api_response(status="error", message="selected_domain_ids required", code=400)
    strict = bool(body.get("strict", getattr(settings, "codecompass_scope_strict_mode", True)))
    scope = DomainScope(
        selected_domain_ids=[str(d) for d in raw_ids],
        strict=strict,
        allow_external_references=bool(
            body.get(
                "allow_external_references",
                getattr(settings, "codecompass_scope_allow_relation_expansion", False),
            )
        ),
        max_external_reference_chunks=int(
            body.get(
                "max_external_reference_chunks",
                getattr(settings, "codecompass_scope_max_external_reference_chunks", 2),
            )
        ),
        requested_by="api_preview",
    )
    resolved = _resolver().resolve(scope)
    payload = resolved.as_dict()
    payload["preview_only"] = True
    if strict and not resolved.ok:
        return api_response(payload, status="error", message="domain_scope_violation", code=422)
    return api_response(payload)
