from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import check_auth, get_request_auth_context
from agent.common.errors import api_response
from agent.services.terminal_policy_service import get_terminal_policy_service
from agent.services.terminal_session_service import get_terminal_session_service
from agent.services.terminal_target_service import get_terminal_target_service

terminal_bp = Blueprint("terminal", __name__)


def _user_ctx() -> dict:
    user = dict(get_request_auth_context() or {})
    if user:
        return user
    return {"sub": "anonymous", "role": "user"}


@terminal_bp.route("/terminal/targets", methods=["GET"])
@check_auth
def list_terminal_targets():
    user_ctx = _user_ctx()
    cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {})

    raw_targets = get_terminal_target_service().list_targets(cfg=current_app.config)
    policy_service = get_terminal_policy_service()
    visible_targets: list[dict] = []
    for target in raw_targets:
        decision = policy_service.evaluate(
            user_ctx=user_ctx,
            operation="list",
            target_type=str(target.get("target_type") or ""),
            target_id=str(target.get("target_id") or ""),
            cfg=cfg,
        )
        if not decision.allow:
            continue
        entry = dict(target)
        entry["capabilities"] = {
            "create": policy_service.evaluate(
                user_ctx=user_ctx, operation="create",
                target_type=entry["target_type"], target_id=entry["target_id"], cfg=cfg,
            ).allow,
            "attach": policy_service.evaluate(
                user_ctx=user_ctx, operation="attach",
                target_type=entry["target_type"], target_id=entry["target_id"], cfg=cfg,
            ).allow,
            "read": policy_service.evaluate(
                user_ctx=user_ctx, operation="read",
                target_type=entry["target_type"], target_id=entry["target_id"], cfg=cfg,
            ).allow,
            "write": policy_service.evaluate(
                user_ctx=user_ctx, operation="write",
                target_type=entry["target_type"], target_id=entry["target_id"], cfg=cfg,
            ).allow,
            "kill": policy_service.evaluate(
                user_ctx=user_ctx, operation="kill",
                target_type=entry["target_type"], target_id=entry["target_id"], cfg=cfg,
            ).allow,
        }
        visible_targets.append(entry)

    return api_response(data={"targets": visible_targets})


@terminal_bp.route("/terminal/sessions", methods=["GET"])
@check_auth
def list_terminal_sessions():
    user_ctx = _user_ctx()
    items = [entry.model_dump() for entry in get_terminal_session_service().list_sessions(user_ctx=user_ctx)]
    return api_response(data={"sessions": items})


@terminal_bp.route("/terminal/sessions", methods=["POST"])
@check_auth
def create_terminal_session():
    payload = request.get_json(silent=True) or {}
    target_type = str(payload.get("target_type") or "").strip()
    target_id = str(payload.get("target_id") or "").strip()
    if not target_type or not target_id:
        return api_response(status="error", message="validation_error",
                            data={"reason_code": "terminal_target_required"}, code=400)

    user_ctx = _user_ctx()
    result = get_terminal_session_service().create_session(
        user_ctx=user_ctx,
        target_type=target_type,
        target_id=target_id,
        workspace_path=payload.get("workspace_path"),
        goal_id=payload.get("goal_id"),
        task_id=payload.get("task_id"),
        read_only=bool(payload.get("read_only", False)),
        cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
    )
    if not result.get("ok"):
        code = 403 if result.get("status") == "forbidden" else 400
        return api_response(status="error", message=result.get("status") or "error", data=result, code=code)
    return api_response(data=result)


@terminal_bp.route("/terminal/sessions/<session_id>", methods=["GET"])
@check_auth
def get_terminal_session(session_id: str):
    user_ctx = _user_ctx()
    entry = get_terminal_session_service().get_session(session_id, user_ctx=user_ctx)
    if entry is None:
        return api_response(status="error", message="not_found",
                            data={"reason_code": "terminal_session_not_found"}, code=404)
    return api_response(data={"session": entry.model_dump()})


@terminal_bp.route("/terminal/sessions/<session_id>/input", methods=["POST"])
@check_auth
def send_terminal_input(session_id: str):
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "")
    if not text:
        return api_response(status="error", message="validation_error",
                            data={"reason_code": "terminal_input_text_required"}, code=400)
    user_ctx = _user_ctx()
    result = get_terminal_session_service().send_input(
        session_id,
        text=text,
        user_ctx=user_ctx,
        cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
    )
    if not result.get("ok"):
        code = 403 if result.get("status") == "forbidden" else 404 if result.get("status") == "not_found" else 400
        return api_response(status="error", message=result.get("status") or "error", data=result, code=code)
    return api_response(data=result)


@terminal_bp.route("/terminal/sessions/<session_id>/output", methods=["GET"])
@check_auth
def get_terminal_output(session_id: str):
    lines = max(1, min(int(request.args.get("lines") or 200), 5000))
    user_ctx = _user_ctx()
    result = get_terminal_session_service().get_output(
        session_id,
        user_ctx=user_ctx,
        cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
        lines=lines,
    )
    if not result.get("ok"):
        code = 403 if result.get("status") == "forbidden" else 404 if result.get("status") == "not_found" else 400
        return api_response(status="error", message=result.get("status") or "error", data=result, code=code)
    return api_response(data=result)


@terminal_bp.route("/terminal/sessions/<session_id>/attach-token", methods=["POST"])
@check_auth
def issue_attach_token(session_id: str):
    user_ctx = _user_ctx()
    result = get_terminal_session_service().generate_attach_token(
        session_id,
        user_ctx=user_ctx,
        cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
    )
    if not result.get("ok"):
        code = 403 if result.get("status") == "forbidden" else 404 if result.get("status") == "not_found" else 400
        return api_response(status="error", message=result.get("status") or "error", data=result, code=code)
    return api_response(data=result)


@terminal_bp.route("/terminal/sessions/<session_id>", methods=["DELETE"])
@check_auth
def kill_terminal_session(session_id: str):
    user_ctx = _user_ctx()
    result = get_terminal_session_service().kill_session(
        session_id,
        user_ctx=user_ctx,
        cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
    )
    if not result.get("ok"):
        code = 403 if result.get("status") == "forbidden" else 404 if result.get("status") == "not_found" else 400
        return api_response(status="error", message=result.get("status") or "error", data=result, code=code)
    return api_response(data=result)
