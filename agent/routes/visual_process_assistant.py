"""Versioned Hub APIs for contextual Visual Process assistance."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_service_auth, check_user_auth, get_request_auth_context
from agent.config import settings
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_assistant_service import (
    VisualProcessAssistantError,
    visual_process_assistant_service,
)
from agent.services.visual_process_patch_service import VisualProcessPatchRejected

visual_process_assistant_bp = Blueprint(
    "visual_process_assistant",
    __name__,
    url_prefix="/api/visual-process/assistant/v1",
)

MAX_ASSISTANT_BODY_BYTES = 512 * 1024


def _principal() -> ChatSessionPrincipal:
    identity = dict(get_request_auth_context() or {})
    subject = identity.get("sub") or identity.get("username")
    tenant_id = identity.get("tenant_id") or identity.get("tenant") or identity.get("organization_id") or subject
    try:
        return ChatSessionPrincipal.from_values(tenant_id, subject)
    except ValueError as exc:
        raise VisualProcessAssistantError("assistant_principal_invalid", status_code=403) from exc


def _body(*, required: bool = True) -> dict[str, Any]:
    content_length = int(request.content_length or 0)
    if content_length > MAX_ASSISTANT_BODY_BYTES:
        raise VisualProcessAssistantError("assistant_request_too_large", status_code=413)
    payload = request.get_json(silent=True)
    if payload is None and not required:
        return {}
    if not isinstance(payload, dict):
        raise VisualProcessAssistantError("assistant_json_object_required", status_code=400)
    return payload


def _chat_enabled(target: Callable):
    @wraps(target)
    def wrapped(*args, **kwargs):
        if not settings.visual_process_assistant_chat_enabled:
            return jsonify(
                {
                    "error": "assistant_feature_disabled",
                    "error_code": "assistant_feature_disabled",
                }
            ), 404
        return target(*args, **kwargs)

    return wrapped


def _respond(call: Callable[[], dict[str, Any]], *, status: int = 200):
    try:
        payload = call()
    except VisualProcessAssistantError as exc:
        response = jsonify(exc.as_dict())
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response, exc.status_code
    except VisualProcessPatchRejected as exc:
        response = jsonify(exc.as_dict())
        response.headers["Cache-Control"] = "no-store"
        return response, exc.status_code
    except (TypeError, ValueError) as exc:
        response = jsonify(
            {
                "error": "assistant_contract_invalid",
                "error_code": "assistant_contract_invalid",
                "detail": str(exc)[:1000],
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 422
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@visual_process_assistant_bp.get("/capabilities")
@check_user_auth
def capabilities():
    patches_enabled = settings.visual_process_ai_patches_enabled
    auto_approval_enabled = patches_enabled and settings.visual_process_ai_patch_auto_approval_enabled
    return jsonify(
        {
            "contract_version": "ananta.visual_process_assistant.capabilities.v1",
            "registry_inspector": settings.visual_process_registry_inspector_enabled,
            "hover_help": settings.visual_process_hover_help_enabled,
            "assistant_chat": settings.visual_process_assistant_chat_enabled,
            "ai_patches": patches_enabled,
            "patch_approval_modes": [
                "interactive",
                *(["hub_auto"] if auto_approval_enabled else []),
            ],
            "patch_auto_approval_enabled": auto_approval_enabled,
            "limits": {
                "max_in_flight_per_conversation": 2,
                "max_requests_per_principal_per_minute": 20,
                "retrieval_timeout_ms": settings.visual_process_assistant_retrieval_timeout_ms,
                "model_timeout_ms": settings.visual_process_assistant_model_timeout_ms,
            },
        }
    ), 200


@visual_process_assistant_bp.post("/contexts")
@check_user_auth
@_chat_enabled
def create_context():
    return _respond(
        lambda: visual_process_assistant_service.create_context(
            principal=_principal(),
            graph_id=str((body := _body()).get("graph_id") or ""),
            payload=body,
        ),
        status=201,
    )


@visual_process_assistant_bp.get("/contexts/<context_id>")
@check_user_auth
@_chat_enabled
def get_context(context_id: str):
    return _respond(
        lambda: visual_process_assistant_service.get_context(
            principal=_principal(),
            context_id=context_id,
        )
    )


@visual_process_assistant_bp.post("/conversations")
@check_user_auth
@_chat_enabled
def create_conversation():
    return _respond(
        lambda: visual_process_assistant_service.create_conversation(
            principal=_principal(),
            context_id=str(_body().get("context_id") or ""),
        ),
        status=201,
    )


@visual_process_assistant_bp.get("/conversations/<conversation_id>")
@check_user_auth
@_chat_enabled
def get_conversation(conversation_id: str):
    return _respond(
        lambda: visual_process_assistant_service.get_conversation(
            principal=_principal(),
            conversation_id=conversation_id,
        )
    )


@visual_process_assistant_bp.post("/conversations/<conversation_id>/context-switch")
@check_user_auth
@_chat_enabled
def switch_conversation_context(conversation_id: str):
    return _respond(
        lambda: visual_process_assistant_service.switch_context(
            principal=_principal(),
            conversation_id=conversation_id,
            context_id=str((body := _body()).get("context_id") or ""),
            confirmed=body.get("confirmed") is True,
        )
    )


@visual_process_assistant_bp.post("/conversations/<conversation_id>/questions")
@check_user_auth
@_chat_enabled
def submit_question(conversation_id: str):
    def call() -> dict[str, Any]:
        body = _body()
        return visual_process_assistant_service.submit_question(
            principal=_principal(),
            conversation_id=conversation_id,
            question=str(body.get("question") or ""),
            client_request_id=str(body.get("client_request_id") or ""),
            idempotency_key=str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or ""),
        )

    return _respond(call, status=202)


@visual_process_assistant_bp.get("/requests/<request_id>")
@check_user_auth
@_chat_enabled
def get_request_status(request_id: str):
    return _respond(
        lambda: visual_process_assistant_service.get_request(
            principal=_principal(),
            request_id=request_id,
        )
    )


@visual_process_assistant_bp.post("/requests/<request_id>/cancel")
@check_user_auth
@_chat_enabled
def cancel_request(request_id: str):
    return _respond(
        lambda: visual_process_assistant_service.cancel_request(
            principal=_principal(),
            request_id=request_id,
        )
    )


@visual_process_assistant_bp.post("/requests/<request_id>/retry")
@check_user_auth
@_chat_enabled
def retry_request(request_id: str):
    def call() -> dict[str, Any]:
        body = _body()
        return visual_process_assistant_service.retry_request(
            principal=_principal(),
            request_id=request_id,
            client_request_id=str(body.get("client_request_id") or ""),
            idempotency_key=str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or ""),
        )

    return _respond(call, status=202)


@visual_process_assistant_bp.post("/requests/<request_id>/patch-preview")
@check_user_auth
@_chat_enabled
def preview_patch(request_id: str):
    def call() -> dict[str, Any]:
        body = _body(required=False)
        return visual_process_assistant_service.preview_patch(
            principal=_principal(),
            request_id=request_id,
            patch_payload=body.get("patch"),
            draft_graph_payload=body.get("draft_graph"),
            patch_enabled=settings.visual_process_ai_patches_enabled,
        )

    return _respond(call)


@visual_process_assistant_bp.post("/requests/<request_id>/patch-refresh")
@check_user_auth
@_chat_enabled
def refresh_patch(request_id: str):
    def call() -> dict[str, Any]:
        body = _body()
        return visual_process_assistant_service.refresh_patch_request(
            principal=_principal(),
            request_id=request_id,
            payload=body,
            client_request_id=str(body.get("client_request_id") or ""),
            idempotency_key=str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or ""),
            patch_enabled=settings.visual_process_ai_patches_enabled,
        )

    return _respond(call, status=202)


@visual_process_assistant_bp.post("/requests/<request_id>/patch-decisions")
@check_user_auth
@_chat_enabled
def decide_patch(request_id: str):
    def call() -> dict[str, Any]:
        body = _body()
        return visual_process_assistant_service.decide_patch(
            principal=_principal(),
            request_id=request_id,
            patch_hash=str(body.get("patch_hash") or ""),
            decision=str(body.get("decision") or ""),
            confirmed=body.get("confirmed") is True,
            approval_mode=str(body.get("approval_mode") or "interactive"),
            draft_graph_payload=body.get("draft_graph"),
            patch_enabled=settings.visual_process_ai_patches_enabled,
            auto_approval_enabled=settings.visual_process_ai_patch_auto_approval_enabled,
        )

    return _respond(call)


@visual_process_assistant_bp.post("/worker-results/<task_id>")
@check_service_auth
def accept_worker_result(task_id: str):
    """Authenticated Worker-to-Hub result boundary; never exposed to browsers."""

    return _respond(
        lambda: visual_process_assistant_service.accept_worker_result(
            task_id=task_id,
            result=_body(),
        ),
        status=202,
    )


__all__ = ["visual_process_assistant_bp"]
