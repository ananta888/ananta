"""Authenticated Hub API for knowledge-expert operations."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.knowledge_augmentation_adapters import (
    KnowledgeAugmentationAdapters,
    default_knowledge_augmentation_adapters,
)
from agent.services.knowledge_expert_control_service import KnowledgeExpertControlService

knowledge_experts_bp = Blueprint("knowledge_experts", __name__, url_prefix="/api/knowledge-experts")


@knowledge_experts_bp.get("")
@check_auth
@admin_required
def snapshot():
    try:
        return api_response(data=_service().snapshot(tenant_id=_tenant_id()))
    except (KeyError, ValueError) as exc:
        return _error(exc)


@knowledge_experts_bp.post("/commands")
@check_auth
@admin_required
def command():
    try:
        body = request.get_json(silent=True)
        allowed = {
            "schema",
            "action",
            "bank_id",
            "generation_id",
            "expected_generation_id",
            "reason",
            "confirmed",
        }
        if (
            not isinstance(body, dict)
            or set(body) != allowed
            or body.get("schema") != "ananta.knowledge-expert-control-command.v1"
            or body.get("confirmed") is not True
        ):
            raise ValueError("knowledge_expert_control_request_invalid")
        result = _service().command(
            tenant_id=_tenant_id(),
            action=str(body["action"]),
            bank_id=str(body["bank_id"]),
            generation_id=str(body["generation_id"]),
            expected_generation_id=str(body["expected_generation_id"]),
            reason=str(body["reason"]),
        )
        log_audit(
            "knowledge_expert_control_command",
            {
                "action": result["action"],
                "reason_code": result["reason_code"],
                "task_id": result["task_id"],
            },
        )
        return api_response(data=result, code=202)
    except (KeyError, ValueError) as exc:
        return _error(exc)


@knowledge_experts_bp.post("/decisions")
@check_auth
def decide_augmentation():
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("knowledge_augmentation_request_invalid")
        context_provider = current_app.extensions.get("knowledge_augmentation_context_provider")
        if callable(context_provider):
            trusted_context = dict(context_provider(tenant_id=_tenant_id()))
        else:
            trusted_context = {
                "global_enabled": False,
                "model_enabled": False,
                "task_enabled": False,
                "domain_enabled": False,
                "data_class_enabled": False,
                "runtime_ready": False,
                "expert_selected": False,
                "citation_required": True,
                "rag_available": True,
            }
        return api_response(data=_augmentation_adapters().for_http(body, hub_context=trusted_context))
    except (KeyError, ValueError) as exc:
        return _error(exc)


def _service() -> KnowledgeExpertControlService:
    service = current_app.extensions.get("knowledge_expert_control_service")
    if isinstance(service, KnowledgeExpertControlService):
        return service
    return KnowledgeExpertControlService(_DisabledKnowledgeExpertControl(), enabled=False)


class _DisabledKnowledgeExpertControl:
    def snapshot(self, *, tenant_id: str) -> dict[str, Any]:
        return {
            "rollout_state": "off",
            "active_banks": [],
            "candidate_banks": [],
            "gates": {
                "research_reproduction": "blocked",
                "runtime_capability": "blocked",
                "benchmark": "blocked",
            },
        }

    def command(self, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("knowledge_expert_control_unavailable")


def _augmentation_adapters() -> KnowledgeAugmentationAdapters:
    configured = current_app.extensions.get("knowledge_augmentation_adapters")
    if isinstance(configured, KnowledgeAugmentationAdapters):
        return configured
    return default_knowledge_augmentation_adapters()


def _tenant_id() -> str:
    identity = dict(get_request_auth_context() or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(
        identity.get("tenant_id")
        or identity.get("tenant")
        or subject
    ).strip()
    if not subject or not tenant_id:
        raise ValueError("knowledge_expert_tenant_required")
    return tenant_id


def _error(exc: Exception):
    reason = str(exc).strip("'") or "knowledge_expert_control_failed"
    status = 503 if isinstance(exc, KeyError) else 422
    return api_response(status="error", message=reason, data={"reason_code": reason}, code=status)


__all__ = ["knowledge_experts_bp"]
