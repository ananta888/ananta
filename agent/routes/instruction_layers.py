from __future__ import annotations

import time

from flask import Blueprint, g, request

from agent.auth import check_auth, check_user_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import InstructionOverlayDB, UserInstructionProfileDB
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry

instruction_layers_bp = Blueprint("instruction_layers", __name__)


def _repos():
    return get_repository_registry()


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "").strip()


def _is_admin() -> bool:
    return bool(getattr(g, "is_admin", False))


def _resolve_owner_for_mutation(payload: dict) -> tuple[str | None, object | None]:
    requested_owner = str(payload.get("owner_username") or "").strip() or None
    current_owner = _current_username()
    if requested_owner and requested_owner != current_owner and not _is_admin():
        return None, api_response(status="error", message="forbidden_instruction_owner_scope", code=403)
    return requested_owner or current_owner, None


def _resolve_owner_for_query() -> tuple[str | None, object | None]:
    requested_owner = str(request.args.get("owner_username") or "").strip() or None
    current_owner = _current_username()
    if requested_owner and requested_owner != current_owner and not _is_admin():
        return None, api_response(status="error", message="forbidden_instruction_owner_scope", code=403)
    return requested_owner or current_owner, None


def _serialize_profile(profile: UserInstructionProfileDB) -> dict:
    return {
        "id": profile.id,
        "owner_username": profile.owner_username,
        "name": profile.name,
        "prompt_content": profile.prompt_content,
        "profile_metadata": dict(profile.profile_metadata or {}),
        "is_active": bool(profile.is_active),
        "is_default": bool(profile.is_default),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _serialize_overlay(overlay: InstructionOverlayDB) -> dict:
    service = get_instruction_layer_service()
    return {
        "id": overlay.id,
        "owner_username": overlay.owner_username,
        "name": overlay.name,
        "prompt_content": overlay.prompt_content,
        "overlay_metadata": dict(overlay.overlay_metadata or {}),
        "scope": service.normalize_overlay_scope(overlay.scope),
        "attachment_kind": overlay.attachment_kind,
        "attachment_id": overlay.attachment_id,
        "is_active": bool(overlay.is_active),
        "expires_at": overlay.expires_at,
        "lifecycle": service.overlay_lifecycle_summary(overlay),
        "created_at": overlay.created_at,
        "updated_at": overlay.updated_at,
    }


def _ensure_profile_access(profile: UserInstructionProfileDB | None):
    if profile is None:
        return api_response(status="error", message="instruction_profile_not_found", code=404)
    if _is_admin():
        return None
    if str(profile.owner_username or "").strip() != _current_username():
        return api_response(status="error", message="forbidden_instruction_profile_access", code=403)
    return None


def _ensure_overlay_access(overlay: InstructionOverlayDB | None):
    if overlay is None:
        return api_response(status="error", message="instruction_overlay_not_found", code=404)
    if _is_admin():
        return None
    if str(overlay.owner_username or "").strip() != _current_username():
        return api_response(status="error", message="forbidden_instruction_overlay_access", code=403)
    return None


def _validate_user_layer_or_conflict(*, prompt_content: str, metadata: dict | None):
    validation = get_instruction_layer_service().validate_user_layer_payload(
        prompt_content=str(prompt_content or ""),
        metadata=dict(metadata or {}),
    )
    if validation.get("ok"):
        return validation, None
    return validation, api_response(
        status="error",
        message="instruction_policy_conflict",
        data={
            "reason": validation.get("blocked_reason"),
            "policy_domain": "instruction_layer_safety",
            "forbidden_directives": validation.get("forbidden_directives"),
            "forbidden_metadata_keys": validation.get("forbidden_metadata_keys"),
            "allowed_scope": validation.get("allowed_user_influence_scope"),
            "forbidden_scope": validation.get("forbidden_user_influence_scope"),
            "suggested_fix": [
                "Entferne Direktiven, die Governance/Approval/Security umgehen.",
                "Beschraenke Profil/Overlay-Anweisungen auf Stil, Sprache, Detaillierungsgrad oder Arbeitsmodus.",
            ],
            "hint": (
                "User profile and overlay prompts may influence style/language/detail level only. "
                "Governance, approval, security and tool policy are not overridable."
            ),
        },
        code=409,
    )


def _validate_overlay_binding(*, scope: str, attachment_kind: str | None, attachment_id: str | None):
    service = get_instruction_layer_service()
    supported_kinds = set(service.layer_model()["supported_overlay_attachment_kinds"])
    if attachment_kind and attachment_kind not in supported_kinds:
        return api_response(status="error", message="invalid_overlay_attachment_kind", code=400)
    if attachment_kind in {"task", "goal", "session"} and not attachment_id:
        return api_response(status="error", message="overlay_attachment_id_required", code=400)
    if scope == "session" and attachment_kind != "session":
        return api_response(status="error", message="overlay_scope_requires_session_attachment", code=400)
    if scope == "project":
        if attachment_kind != "usage":
            return api_response(status="error", message="overlay_scope_requires_usage_attachment", code=400)
        if not attachment_id:
            return api_response(status="error", message="overlay_attachment_id_required", code=400)
    return None


@instruction_layers_bp.route("/instruction-layers/model", methods=["GET"])
@check_auth
def instruction_layer_model():
    return api_response(data=get_instruction_layer_service().layer_model())


@instruction_layers_bp.route("/instruction-layers/effective", methods=["GET"])
@check_user_auth
def instruction_layer_effective():
    owner_username, owner_error = _resolve_owner_for_query()
    if owner_error is not None:
        return owner_error

    task_id = str(request.args.get("task_id") or "").strip() or None
    goal_id = str(request.args.get("goal_id") or "").strip() or None
    session_id = str(request.args.get("session_id") or "").strip() or None
    usage_key = str(request.args.get("usage_key") or "").strip() or None
    base_prompt = str(request.args.get("base_prompt") or "instruction-stack-preview").strip()
    explicit_profile_id = str(request.args.get("profile_id") or "").strip() or None
    explicit_overlay_id = str(request.args.get("overlay_id") or "").strip() or None

    task_payload: dict = {
        "id": task_id,
        "goal_id": goal_id,
        "worker_execution_context": {
            "instruction_context": {
                "owner_username": owner_username,
                "profile_id": explicit_profile_id,
                "overlay_id": explicit_overlay_id,
            }
        },
    }
    if task_id:
        task = _repos().task_repo.get_by_id(task_id)
        if task is None:
            return api_response(status="error", message="task_not_found", code=404)
        task_payload = task.model_dump()
        task_payload.setdefault("worker_execution_context", {})
        task_payload["worker_execution_context"].setdefault("instruction_context", {})
        if owner_username:
            task_payload["worker_execution_context"]["instruction_context"]["owner_username"] = owner_username
        if explicit_profile_id:
            task_payload["worker_execution_context"]["instruction_context"]["profile_id"] = explicit_profile_id
        if explicit_overlay_id:
            task_payload["worker_execution_context"]["instruction_context"]["overlay_id"] = explicit_overlay_id
    elif goal_id:
        goal = _repos().goal_repo.get_by_id(goal_id)
        if goal is None:
            return api_response(status="error", message="goal_not_found", code=404)
        execution_preferences = dict(goal.execution_preferences or {})
        instruction_context = dict(execution_preferences.get("instruction_context") or {})
        if owner_username:
            instruction_context["owner_username"] = owner_username
        if explicit_profile_id:
            instruction_context["profile_id"] = explicit_profile_id
        if explicit_overlay_id:
            instruction_context["overlay_id"] = explicit_overlay_id
        task_payload["goal_id"] = goal.id
        task_payload["worker_execution_context"]["instruction_context"] = instruction_context
    elif not owner_username:
        return api_response(status="error", message="owner_username_required", code=400)

    assembled = get_instruction_layer_service().assemble_for_task(
        task=task_payload,
        base_prompt=base_prompt,
        system_prompt=None,
        session_id=session_id,
        usage_key=usage_key,
    )
    return api_response(data=assembled)


@instruction_layers_bp.route("/instruction-profiles", methods=["GET"])
@check_user_auth
def list_instruction_profiles():
    owner_username, owner_error = _resolve_owner_for_query()
    if owner_error is not None:
        return owner_error
    profiles = _repos().user_instruction_profile_repo.list_by_owner(owner_username, include_inactive=True)
    return api_response(data=[_serialize_profile(profile) for profile in profiles])


@instruction_layers_bp.route("/instruction-profiles/examples", methods=["GET"])
@check_user_auth
def list_instruction_profile_examples():
    return api_response(data=get_instruction_layer_service().profile_examples())


@instruction_layers_bp.route("/instruction-profiles", methods=["POST"])
@check_user_auth
def create_instruction_profile():
    payload = request.get_json(silent=True) or {}
    owner_username, owner_error = _resolve_owner_for_mutation(payload)
    if owner_error is not None:
        return owner_error
    name = str(payload.get("name") or "").strip()
    prompt_content = str(payload.get("prompt_content") or "").strip()
    if not name or not prompt_content:
        return api_response(status="error", message="name_and_prompt_required", code=400)
    profile_metadata = get_instruction_layer_service().normalize_profile_metadata(payload.get("profile_metadata"))
    validation, validation_error = _validate_user_layer_or_conflict(
        prompt_content=prompt_content,
        metadata=profile_metadata,
    )
    if validation_error is not None:
        return validation_error

    profile = UserInstructionProfileDB(
        owner_username=owner_username,
        name=name,
        prompt_content=prompt_content,
        profile_metadata=profile_metadata,
        is_active=bool(payload.get("is_active", True)),
        is_default=bool(payload.get("is_default", False)),
    )
    profile = _repos().user_instruction_profile_repo.save(profile)
    if profile.is_default:
        selected = _repos().user_instruction_profile_repo.set_default_profile(owner_username, profile.id)
        if selected is not None:
            profile = selected
    log_audit(
        "instruction_profile_created",
        {"profile_id": profile.id, "owner_username": owner_username, "is_default": bool(profile.is_default)},
    )
    return api_response(data={**_serialize_profile(profile), "validation": validation}, code=201)


@instruction_layers_bp.route("/instruction-profiles/<profile_id>", methods=["GET"])
@check_user_auth
def get_instruction_profile(profile_id: str):
    profile = _repos().user_instruction_profile_repo.get_by_id(profile_id)
    error = _ensure_profile_access(profile)
    if error is not None:
        return error
    return api_response(data=_serialize_profile(profile))


@instruction_layers_bp.route("/instruction-profiles/<profile_id>", methods=["PATCH"])
@check_user_auth
def patch_instruction_profile(profile_id: str):
    profile = _repos().user_instruction_profile_repo.get_by_id(profile_id)
    error = _ensure_profile_access(profile)
    if error is not None:
        return error
    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        profile.name = str(payload.get("name") or "").strip() or profile.name
    if "prompt_content" in payload:
        profile.prompt_content = str(payload.get("prompt_content") or "").strip()
    if "profile_metadata" in payload:
        profile.profile_metadata = get_instruction_layer_service().normalize_profile_metadata(payload.get("profile_metadata"))
    if "is_active" in payload:
        profile.is_active = bool(payload.get("is_active"))
    if "is_default" in payload:
        profile.is_default = bool(payload.get("is_default"))
        if profile.is_default:
            profile.is_active = True
    validation, validation_error = _validate_user_layer_or_conflict(
        prompt_content=profile.prompt_content,
        metadata=dict(profile.profile_metadata or {}),
    )
    if validation_error is not None:
        return validation_error
    profile = _repos().user_instruction_profile_repo.save(profile)
    if profile.is_default:
        selected = _repos().user_instruction_profile_repo.set_default_profile(profile.owner_username, profile.id)
        if selected is not None:
            profile = selected
    log_audit("instruction_profile_updated", {"profile_id": profile.id, "owner_username": profile.owner_username})
    return api_response(data={**_serialize_profile(profile), "validation": validation})


@instruction_layers_bp.route("/instruction-profiles/<profile_id>", methods=["DELETE"])
@check_user_auth
def delete_instruction_profile(profile_id: str):
    profile = _repos().user_instruction_profile_repo.get_by_id(profile_id)
    error = _ensure_profile_access(profile)
    if error is not None:
        return error
    deleted = _repos().user_instruction_profile_repo.delete(profile_id)
    if not deleted:
        return api_response(status="error", message="instruction_profile_not_found", code=404)
    log_audit("instruction_profile_deleted", {"profile_id": profile_id, "owner_username": profile.owner_username})
    return api_response(data={"status": "deleted", "id": profile_id})


@instruction_layers_bp.route("/instruction-profiles/<profile_id>/select", methods=["POST"])
@check_user_auth
def select_instruction_profile(profile_id: str):
    profile = _repos().user_instruction_profile_repo.get_by_id(profile_id)
    error = _ensure_profile_access(profile)
    if error is not None:
        return error
    selected = _repos().user_instruction_profile_repo.set_default_profile(profile.owner_username, profile.id)
    if selected is None:
        return api_response(status="error", message="instruction_profile_not_found", code=404)
    log_audit("instruction_profile_selected", {"profile_id": selected.id, "owner_username": selected.owner_username})
    return api_response(data=_serialize_profile(selected))


@instruction_layers_bp.route("/instruction-overlays", methods=["GET"])
@check_user_auth
def list_instruction_overlays():
    owner_username, owner_error = _resolve_owner_for_query()
    if owner_error is not None:
        return owner_error
    attachment_kind = str(request.args.get("attachment_kind") or "").strip() or None
    attachment_id = str(request.args.get("attachment_id") or "").strip() or None
    overlays = _repos().instruction_overlay_repo.list_by_owner(
        owner_username,
        include_inactive=True,
        attachment_kind=attachment_kind,
        attachment_id=attachment_id,
        include_expired=True,
    )
    return api_response(data=[_serialize_overlay(overlay) for overlay in overlays])


@instruction_layers_bp.route("/instruction-overlays", methods=["POST"])
@check_user_auth
def create_instruction_overlay():
    payload = request.get_json(silent=True) or {}
    owner_username, owner_error = _resolve_owner_for_mutation(payload)
    if owner_error is not None:
        return owner_error
    name = str(payload.get("name") or "").strip()
    prompt_content = str(payload.get("prompt_content") or "").strip()
    if not name or not prompt_content:
        return api_response(status="error", message="name_and_prompt_required", code=400)
    service = get_instruction_layer_service()
    attachment_kind = str(payload.get("attachment_kind") or "").strip() or None
    attachment_id = str(payload.get("attachment_id") or "").strip() or None
    scope = service.normalize_overlay_scope(payload.get("scope"))
    binding_error = _validate_overlay_binding(scope=scope, attachment_kind=attachment_kind, attachment_id=attachment_id)
    if binding_error is not None:
        return binding_error

    overlay_metadata = service.normalize_overlay_metadata(payload.get("overlay_metadata"))
    validation, validation_error = _validate_user_layer_or_conflict(
        prompt_content=prompt_content,
        metadata=overlay_metadata,
    )
    if validation_error is not None:
        return validation_error

    expires_at = payload.get("expires_at")
    expires_at = float(expires_at) if expires_at is not None else None
    overlay = InstructionOverlayDB(
        owner_username=owner_username,
        name=name,
        prompt_content=prompt_content,
        overlay_metadata=overlay_metadata,
        scope=scope,
        attachment_kind=attachment_kind,
        attachment_id=attachment_id,
        is_active=bool(payload.get("is_active", True)),
        expires_at=expires_at,
    )
    overlay = _repos().instruction_overlay_repo.save(overlay)
    log_audit(
        "instruction_overlay_created",
        {
            "overlay_id": overlay.id,
            "owner_username": owner_username,
            "attachment_kind": overlay.attachment_kind,
            "attachment_id": overlay.attachment_id,
        },
    )
    return api_response(data={**_serialize_overlay(overlay), "validation": validation}, code=201)


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>", methods=["GET"])
@check_user_auth
def get_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    return api_response(data=_serialize_overlay(overlay))


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>", methods=["PATCH"])
@check_user_auth
def patch_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        overlay.name = str(payload.get("name") or "").strip() or overlay.name
    if "prompt_content" in payload:
        overlay.prompt_content = str(payload.get("prompt_content") or "").strip()
    if "overlay_metadata" in payload:
        overlay.overlay_metadata = get_instruction_layer_service().normalize_overlay_metadata(payload.get("overlay_metadata"))
    if "scope" in payload:
        overlay.scope = get_instruction_layer_service().normalize_overlay_scope(payload.get("scope"))
    if "attachment_kind" in payload:
        attachment_kind = str(payload.get("attachment_kind") or "").strip() or None
        overlay.attachment_kind = attachment_kind
    if "attachment_id" in payload:
        overlay.attachment_id = str(payload.get("attachment_id") or "").strip() or None
    binding_error = _validate_overlay_binding(
        scope=get_instruction_layer_service().normalize_overlay_scope(overlay.scope),
        attachment_kind=str(overlay.attachment_kind or "").strip() or None,
        attachment_id=str(overlay.attachment_id or "").strip() or None,
    )
    if binding_error is not None:
        return binding_error
    if "is_active" in payload:
        overlay.is_active = bool(payload.get("is_active"))
    if "expires_at" in payload:
        overlay.expires_at = float(payload.get("expires_at")) if payload.get("expires_at") is not None else None

    validation, validation_error = _validate_user_layer_or_conflict(
        prompt_content=overlay.prompt_content,
        metadata=dict(overlay.overlay_metadata or {}),
    )
    if validation_error is not None:
        return validation_error

    overlay = _repos().instruction_overlay_repo.save(overlay)
    log_audit("instruction_overlay_updated", {"overlay_id": overlay.id, "owner_username": overlay.owner_username})
    return api_response(data={**_serialize_overlay(overlay), "validation": validation})


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>", methods=["DELETE"])
@check_user_auth
def delete_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    deleted = _repos().instruction_overlay_repo.delete(overlay_id)
    if not deleted:
        return api_response(status="error", message="instruction_overlay_not_found", code=404)
    log_audit("instruction_overlay_deleted", {"overlay_id": overlay_id, "owner_username": overlay.owner_username})
    return api_response(data={"status": "deleted", "id": overlay_id})


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>/select", methods=["POST"])
@check_user_auth
def select_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    payload = request.get_json(silent=True) or {}
    if "attachment_kind" in payload:
        attachment_kind = str(payload.get("attachment_kind") or "").strip() or None
        overlay.attachment_kind = attachment_kind
    if "attachment_id" in payload:
        overlay.attachment_id = str(payload.get("attachment_id") or "").strip() or None
    binding_error = _validate_overlay_binding(
        scope=get_instruction_layer_service().normalize_overlay_scope(overlay.scope),
        attachment_kind=str(overlay.attachment_kind or "").strip() or None,
        attachment_id=str(overlay.attachment_id or "").strip() or None,
    )
    if binding_error is not None:
        return binding_error
    overlay.is_active = True
    overlay.updated_at = time.time()
    overlay = _repos().instruction_overlay_repo.save(overlay)
    log_audit(
        "instruction_overlay_selected",
        {
            "overlay_id": overlay.id,
            "owner_username": overlay.owner_username,
            "attachment_kind": overlay.attachment_kind,
            "attachment_id": overlay.attachment_id,
        },
    )
    return api_response(data=_serialize_overlay(overlay))


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>/attach", methods=["POST"])
@check_user_auth
def attach_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    payload = request.get_json(silent=True) or {}
    attachment_kind = str(payload.get("attachment_kind") or "").strip() or None
    attachment_id = str(payload.get("attachment_id") or "").strip() or None
    if not attachment_kind:
        return api_response(status="error", message="overlay_attachment_kind_required", code=400)
    binding_error = _validate_overlay_binding(
        scope=get_instruction_layer_service().normalize_overlay_scope(overlay.scope),
        attachment_kind=attachment_kind,
        attachment_id=attachment_id,
    )
    if binding_error is not None:
        return binding_error
    overlay.attachment_kind = attachment_kind
    overlay.attachment_id = attachment_id
    overlay.is_active = True
    overlay.updated_at = time.time()
    overlay = _repos().instruction_overlay_repo.save(overlay)
    log_audit(
        "instruction_overlay_attached",
        {
            "overlay_id": overlay.id,
            "owner_username": overlay.owner_username,
            "attachment_kind": overlay.attachment_kind,
            "attachment_id": overlay.attachment_id,
        },
    )
    return api_response(data=_serialize_overlay(overlay))


@instruction_layers_bp.route("/instruction-overlays/<overlay_id>/detach", methods=["POST"])
@check_user_auth
def detach_instruction_overlay(overlay_id: str):
    overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
    error = _ensure_overlay_access(overlay)
    if error is not None:
        return error
    overlay.attachment_kind = None
    overlay.attachment_id = None
    overlay.updated_at = time.time()
    overlay = _repos().instruction_overlay_repo.save(overlay)
    log_audit("instruction_overlay_detached", {"overlay_id": overlay.id, "owner_username": overlay.owner_username})
    return api_response(data=_serialize_overlay(overlay))


@instruction_layers_bp.route("/goals/<goal_id>/instruction-selection", methods=["POST"])
@check_user_auth
def set_goal_instruction_selection(goal_id: str):
    payload = request.get_json(silent=True) or {}
    owner_username, owner_error = _resolve_owner_for_mutation(payload)
    if owner_error is not None:
        return owner_error
    profile_id = str(payload.get("profile_id") or "").strip() or None
    overlay_id = str(payload.get("overlay_id") or "").strip() or None
    try:
        summary = get_instruction_layer_service().set_goal_selection(
            goal_id=goal_id,
            owner_username=owner_username,
            profile_id=profile_id,
            overlay_id=overlay_id,
            actor=_current_username(),
        )
    except ValueError as exc:
        message = str(exc)
        if message in {"goal_not_found", "profile_not_found", "overlay_not_found"}:
            return api_response(status="error", message=message, code=404)
        if message in {"profile_owner_mismatch", "overlay_owner_mismatch"}:
            return api_response(status="error", message=message, code=409)
        return api_response(status="error", message=message, code=400)
    return api_response(data=summary)


@instruction_layers_bp.route("/tasks/<task_id>/instruction-selection", methods=["POST"])
@check_user_auth
def set_task_instruction_selection(task_id: str):
    payload = request.get_json(silent=True) or {}
    owner_username, owner_error = _resolve_owner_for_mutation(payload)
    if owner_error is not None:
        return owner_error
    profile_id = str(payload.get("profile_id") or "").strip() or None
    overlay_id = str(payload.get("overlay_id") or "").strip() or None
    try:
        summary = get_instruction_layer_service().set_task_selection(
            task_id=task_id,
            owner_username=owner_username,
            profile_id=profile_id,
            overlay_id=overlay_id,
            actor=_current_username(),
        )
    except ValueError as exc:
        message = str(exc)
        if message in {"task_not_found", "profile_not_found", "overlay_not_found"}:
            return api_response(status="error", message=message, code=404)
        if message in {"profile_owner_mismatch", "overlay_owner_mismatch"}:
            return api_response(status="error", message=message, code=409)
        return api_response(status="error", message=message, code=400)
    return api_response(data=summary)
