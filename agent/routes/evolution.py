from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.evolution import EvolutionContextBuildOptions, EvolutionTrigger, EvolutionTriggerType
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

evolution_bp = Blueprint("evolution", __name__)


def _services():
    return get_core_services()


def _repos():
    return get_repository_registry()


def _evolution_config() -> dict:
    return dict((current_app.config.get("AGENT_CONFIG", {}) or {}).get("evolution") or {})


def _actor() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


@evolution_bp.route("/evolution/providers", methods=["GET"])
@check_auth
def list_evolution_providers():
    return api_response(
        data={
            "providers": _services().evolution_service.list_providers(),
            "health": _services().evolution_service.provider_health(),
            "config": _evolution_config(),
        }
    )


@evolution_bp.route("/evolution/providers/<provider_name>", methods=["GET"])
@check_auth
def get_evolution_provider(provider_name: str):
    try:
        return api_response(data=_services().evolution_service.provider_health(provider_name))
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
        return api_response(status="error", message=str(exc), code=400)

    return api_response(
        data={
            "run_id": result.run_id,
            "provider_name": result.provider_name,
            "status": result.status,
            "proposal_ids": result.proposal_ids,
            "summary": result.result.summary,
        }
    )


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
        return api_response(status="error", message=str(exc), code=403)
    except Exception as exc:
        return api_response(status="error", message=str(exc), code=400)

    return api_response(data=result.model_dump(mode="json"))


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
        return api_response(status="error", message=str(exc), code=403)
    except Exception as exc:
        return api_response(status="error", message=str(exc), code=400)

    return api_response(data=result.model_dump(mode="json"))
