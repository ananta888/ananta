from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.evolution import (
    EvolutionContextBuildOptions,
    EvolutionTrigger,
    EvolutionTriggerType,
    UnsupportedEvolutionOperation,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

evolution_bp = Blueprint("evolution", __name__)


def _services():
    return get_core_services()


def _repos():
    return get_repository_registry()


def _evolution_config() -> dict:
    return dict((current_app.config.get("AGENT_CONFIG", {}) or {}).get("evolution") or {})


def _public_evolution_config() -> dict:
    return _sanitize_evolution_config(_evolution_config())


def _sanitize_evolution_config(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in {"bearer_token", "token", "secret", "password", "api_key", "apikey"}:
                sanitized[key] = "***REDACTED***" if item else item
            elif normalized == "headers" and isinstance(item, dict):
                sanitized[key] = {
                    str(header_name): "***REDACTED***"
                    for header_name in item.keys()
                }
            else:
                sanitized[key] = _sanitize_evolution_config(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_evolution_config(item) for item in value]
    return value


def _actor() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


def _evolution_error_response(exc: Exception, *, default_code: int = 400):
    error_code = _evolution_error_code(exc)
    payload = {
        "error_code": error_code,
        "error_type": type(exc).__name__,
    }
    if hasattr(exc, "transient"):
        payload["transient"] = bool(getattr(exc, "transient"))
        payload["retryable"] = bool(getattr(exc, "transient"))
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        payload["provider_status_code"] = status_code
    return api_response(status="error", message=str(exc), data=payload, code=_evolution_http_status(exc, default_code))


def _evolution_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code:
        return f"provider_{str(code).strip().lower()}"
    if isinstance(exc, UnsupportedEvolutionOperation):
        return "provider_operation_not_supported"
    if isinstance(exc, PermissionError):
        return "evolution_policy_blocked"
    return "evolution_error"


def _evolution_http_status(exc: Exception, default_code: int) -> int:
    if isinstance(exc, PermissionError | UnsupportedEvolutionOperation):
        return 403
    code = getattr(exc, "code", None)
    if code in {"timeout", "connection_error", "http_error", "invalid_response"}:
        return 502
    return default_code


@evolution_bp.route("/evolution/providers", methods=["GET"])
@check_auth
def list_evolution_providers():
    cfg = _evolution_config()
    return api_response(
        data={
            "providers": _services().evolution_service.list_providers_with_config({"evolution": cfg}),
            "health": _services().evolution_service.provider_health_with_config(config={"evolution": cfg}),
            "config": _public_evolution_config(),
        }
    )


@evolution_bp.route("/evolution/providers/<provider_name>", methods=["GET"])
@check_auth
def get_evolution_provider(provider_name: str):
    try:
        cfg = _evolution_config()
        return api_response(data=_services().evolution_service.provider_health_with_config(provider_name, config={"evolution": cfg}))
    except Exception as exc:
        return api_response(status="error", message=str(exc), code=404)


@evolution_bp.route("/tasks/<task_id>/evolution", methods=["GET"])
@check_auth
def task_evolution_read_model(task_id: str):
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_services().evolution_service.task_read_model(task_id))


@evolution_bp.route("/tasks/<task_id>/evolution/analyze", methods=["POST"])
@check_auth
def analyze_task_evolution(task_id: str):
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    cfg = _evolution_config()
    if not bool(cfg.get("enabled", True)):
        return api_response(status="error", message="evolution_disabled", code=403)
    if not bool(cfg.get("manual_triggers_enabled", True)):
        return api_response(status="error", message="manual_evolution_triggers_disabled", code=403)

    payload = request.get_json(silent=True) or {}
    provider_name = str(payload.get("provider_name") or payload.get("provider") or "").strip() or None
    objective = str(payload.get("objective") or "").strip() or None
    trigger_type = str(payload.get("trigger_type") or EvolutionTriggerType.MANUAL.value).strip()
    try:
        trigger_type_value = EvolutionTriggerType(trigger_type)
    except ValueError:
        return api_response(status="error", message="invalid_trigger_type", code=400)

    options_payload = payload.get("context_options") if isinstance(payload.get("context_options"), dict) else {}
    options = EvolutionContextBuildOptions(
        audit_limit=int(options_payload.get("audit_limit") or 50),
        verification_limit=int(options_payload.get("verification_limit") or 10),
        artifact_limit=int(options_payload.get("artifact_limit") or 20),
        include_audit_details=bool(options_payload.get("include_audit_details", False)),
    )
    trigger = EvolutionTrigger(
        trigger_type=trigger_type_value,
        source=str(payload.get("trigger_source") or "manual_api"),
        actor=_actor(),
        reason=str(payload.get("reason") or "").strip() or None,
        trigger_metadata=payload.get("trigger_metadata") if isinstance(payload.get("trigger_metadata"), dict) else {},
    )
    provider_config = {"default_provider": cfg.get("default_provider") or provider_name, "evolution": cfg}
    try:
        result = _services().evolution_service.analyze_task(
            task_id,
            objective=objective,
            provider_name=provider_name,
            config=provider_config,
            options=options,
            trigger=trigger,
            persist=True,
        )
    except KeyError as exc:
        return api_response(status="error", message=str(exc).strip("'"), code=404)
    except Exception as exc:
        return _evolution_error_response(exc)

    return api_response(
        data={
            "run_id": result.run_id,
            "provider_name": result.provider_name,
            "status": result.status,
            "proposal_ids": result.proposal_ids,
            "summary": result.result.summary,
            "trace_id": (_repos().evolution_run_repo.get_by_id(result.run_id).trace_id if _repos().evolution_run_repo.get_by_id(result.run_id) else None),
            "provider_metadata": result.result.provider_metadata,
            "review_status": _analysis_review_status(result),
            "proposals": _analysis_proposal_links(task_id, result),
        }
    )


def _analysis_review_status(result) -> dict:
    proposals = list(result.result.proposals or [])
    review_required = any(bool(proposal.requires_review) for proposal in proposals)
    return {
        "required": review_required,
        "status": "review_required" if review_required else "not_required",
        "proposal_count": len(proposals),
    }


def _analysis_proposal_links(task_id: str, result) -> list[dict]:
    proposals = list(result.result.proposals or [])
    proposal_ids = list(result.proposal_ids or [])
    linked = []
    for index, proposal_id in enumerate(proposal_ids):
        proposal = proposals[index] if index < len(proposals) else None
        linked.append(
            {
                "proposal_id": proposal_id,
                "title": proposal.title if proposal is not None else "",
                "risk_level": proposal.risk_level if proposal is not None else "unknown",
                "requires_review": bool(proposal.requires_review) if proposal is not None else True,
                "links": {
                    "read_model": f"/tasks/{task_id}/evolution",
                    "validate": f"/tasks/{task_id}/evolution/proposals/{proposal_id}/validate",
                    "apply": f"/tasks/{task_id}/evolution/proposals/{proposal_id}/apply",
                },
            }
        )
    return linked


@evolution_bp.route("/tasks/<task_id>/evolution/proposals/<proposal_id>/validate", methods=["POST"])
@check_auth
def validate_task_evolution_proposal(task_id: str, proposal_id: str):
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    cfg = _evolution_config()
    if not bool(cfg.get("enabled", True)):
        return api_response(status="error", message="evolution_disabled", code=403)
    if not bool(cfg.get("validate_allowed", True)):
        return api_response(status="error", message="evolution_validation_disabled", code=403)

    payload = request.get_json(silent=True) or {}
    provider_name = str(payload.get("provider_name") or payload.get("provider") or "").strip() or None
    trigger = EvolutionTrigger(
        trigger_type=EvolutionTriggerType.POLICY_REQUEST,
        source=str(payload.get("trigger_source") or "manual_api"),
        actor=_actor(),
        reason=str(payload.get("reason") or "proposal_validation_requested").strip(),
        trigger_metadata=payload.get("trigger_metadata") if isinstance(payload.get("trigger_metadata"), dict) else {},
    )
    try:
        result = _services().evolution_service.validate_persisted_proposal(
            task_id,
            proposal_id,
            provider_name=provider_name,
            config={"default_provider": cfg.get("default_provider") or provider_name, "evolution": cfg},
            trigger=trigger,
        )
    except KeyError as exc:
        return api_response(status="error", message=str(exc).strip("'"), code=404)
    except PermissionError as exc:
        return _evolution_error_response(exc, default_code=403)
    except Exception as exc:
        return _evolution_error_response(exc)

    return api_response(data=result.model_dump(mode="json"))


@evolution_bp.route("/tasks/<task_id>/evolution/proposals/<proposal_id>/review", methods=["POST"])
@check_auth
def review_task_evolution_proposal(task_id: str, proposal_id: str):
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    comment = str(payload.get("comment") or "").strip() or None
    if action not in {"approve", "reject"}:
        return api_response(status="error", message="invalid_review_action", code=400)
    try:
        proposal = _services().evolution_service.review_persisted_proposal(
            task_id,
            proposal_id,
            action=action,
            actor=_actor(),
            comment=comment,
        )
    except KeyError as exc:
        return api_response(status="error", message=str(exc).strip("'"), code=404)
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=400)

    return api_response(data=proposal)


@evolution_bp.route("/tasks/<task_id>/evolution/proposals/<proposal_id>/apply", methods=["POST"])
@check_auth
def apply_task_evolution_proposal(task_id: str, proposal_id: str):
    task = _repos().task_repo.get_by_id(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    cfg = _evolution_config()
    if not bool(cfg.get("enabled", True)):
        return api_response(status="error", message="evolution_disabled", code=403)
    if not bool(cfg.get("apply_allowed", False)):
        return api_response(status="error", message="evolution_apply_disabled", code=403)

    payload = request.get_json(silent=True) or {}
    provider_name = str(payload.get("provider_name") or payload.get("provider") or "").strip() or None
    trigger = EvolutionTrigger(
        trigger_type=EvolutionTriggerType.POLICY_REQUEST,
        source=str(payload.get("trigger_source") or "manual_api"),
        actor=_actor(),
        reason=str(payload.get("reason") or "proposal_apply_requested").strip(),
        trigger_metadata=payload.get("trigger_metadata") if isinstance(payload.get("trigger_metadata"), dict) else {},
    )
    try:
        result = _services().evolution_service.apply_persisted_proposal(
            task_id,
            proposal_id,
            provider_name=provider_name,
            config={"default_provider": cfg.get("default_provider") or provider_name, "evolution": cfg},
            trigger=trigger,
        )
    except KeyError as exc:
        return api_response(status="error", message=str(exc).strip("'"), code=404)
    except PermissionError as exc:
        return _evolution_error_response(exc, default_code=403)
    except Exception as exc:
        return _evolution_error_response(exc)

    return api_response(data=result.model_dump(mode="json"))
