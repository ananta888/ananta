"""Versioned HTTP API for the TaskDB-backed Kanban projection."""

from __future__ import annotations

from functools import wraps
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request
from pydantic import BaseModel, ValidationError

from ananta_contracts.kanban import (
    AssignCardCommand,
    BlockCardCommand,
    CommentCardCommand,
    CompleteCardCommand,
    CreateBoardCommand,
    CreateCardCommand,
    KanbanColumnId,
    MoveCardCommand,
    SetDependenciesCommand,
)
from agent.auth import check_auth
from agent.services.kanban_authorization_service import (
    KanbanAuthorizationService,
    KanbanPrincipal,
)
from agent.services.kanban_feature_flags import KanbanFeatureFlags
from agent.services.kanban_event_stream_service import (
    get_kanban_event_stream_service,
)
from agent.services.kanban_projection_service import KanbanProjectionService, KanbanServiceError
from agent.services.surface_rate_limit_policy import (
    KANBAN_EVENT_RECONNECT,
    KANBAN_WRITE,
    is_auth_disabled,
    surface_rate_limit_policy,
)


kanban_bp = Blueprint("kanban_v1", __name__, url_prefix="/api/v1/kanban")


def _success(value: BaseModel, status_code: int = 200):
    return jsonify({"data": value.model_dump(mode="json")}), status_code


def _error(code: str, message: str, status_code: int, details: dict[str, Any] | None = None):
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return jsonify(payload), status_code


def _principal() -> KanbanPrincipal:
    auth_payload = getattr(g, "auth_payload", None)
    auth_payload = auth_payload if isinstance(auth_payload, dict) else {}
    user = getattr(g, "user", None)
    user = user if isinstance(user, dict) else {}
    payload = {**user, **auth_payload}
    raw = payload.get("capabilities")
    raw = raw if isinstance(raw, (list, tuple, set)) else ()
    return KanbanPrincipal(
        subject=str(
            payload.get("sub")
            or payload.get("id")
            or payload.get("username")
            or "anonymous"
        ),
        role=str(payload.get("role") or ("admin" if getattr(g, "is_admin", False) else "user")),
        tenant_id=str(payload["tenant_id"]) if payload.get("tenant_id") else None,
        team_id=str(payload["team_id"]) if payload.get("team_id") else None,
        declared_capabilities=KanbanAuthorizationService.parse_declared_capabilities(raw),
        is_admin=bool(getattr(g, "is_admin", False)),
    )


def _parse(model):
    return model.model_validate(request.get_json(silent=True) or {})


def _limit() -> int:
    try:
        return int(request.args.get("limit", "50"))
    except ValueError as exc:
        raise KanbanServiceError(
            "kanban_limit_invalid", "limit must be an integer", status_code=400
        ) from exc


def _bool_filter(name: str) -> bool | None:
    value = request.args.get(name)
    if value is None:
        return None
    if value.lower() in {"true", "1"}:
        return True
    if value.lower() in {"false", "0"}:
        return False
    raise KanbanServiceError(
        "kanban_filter_invalid", f"{name} must be true or false", status_code=400
    )


def _event_cursor() -> tuple[int, int]:
    unknown = set(request.args) - {"after_sequence", "limit"}
    if unknown:
        raise KanbanServiceError(
            "kanban_event_cursor_invalid",
            "event query contains unsupported parameters",
            status_code=400,
        )
    raw_after = (
        request.args.get("after_sequence")
        or request.headers.get("Last-Event-ID")
        or "0"
    )
    try:
        after_sequence = int(str(raw_after).strip())
        limit = int(request.args.get("limit", "100"))
    except ValueError as exc:
        raise KanbanServiceError(
            "kanban_event_cursor_invalid",
            "event cursor and limit must be integers",
            status_code=400,
        ) from exc
    if after_sequence < 0 or not 1 <= limit <= 500:
        raise KanbanServiceError(
            "kanban_event_cursor_invalid",
            "event cursor or limit is out of range",
            status_code=400,
        )
    return after_sequence, limit


def _rate_limit_response(namespace: str):
    decision = surface_rate_limit_policy.consume(
        config=current_app.config,
        namespace=namespace,
        auth_payload=getattr(g, "auth_payload", None),
        user=getattr(g, "user", None),
        remote_addr=request.remote_addr,
    )
    if decision.allowed:
        return None
    response, status_code = _error(
        "rate_limit_exceeded",
        "Kanban surface rate limit exceeded",
        429,
        {"retry_after_seconds": decision.retry_after_seconds},
    )
    response.headers["Retry-After"] = str(decision.retry_after_seconds)
    return response, status_code


def _endpoint(
    *,
    write: bool = False,
    rate_limit_namespace: str | None = None,
):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            flags = KanbanFeatureFlags.from_config(current_app.config)
            if not flags.api_enabled:
                return _error("kanban_feature_disabled", "Kanban API is disabled", 404)
            if write and not flags.write_enabled:
                return _error("kanban_write_disabled", "Kanban writes are disabled", 404)
            if write and is_auth_disabled(getattr(g, "auth_payload", None)):
                return _error(
                    "kanban_auth_required",
                    "Kanban writes require authenticated identity",
                    403,
                )
            effective_rate_limit = (
                rate_limit_namespace
                if rate_limit_namespace is not None
                else KANBAN_WRITE if write else None
            )
            if effective_rate_limit is not None:
                rate_limited = _rate_limit_response(effective_rate_limit)
                if rate_limited is not None:
                    return rate_limited
            try:
                return function(*args, **kwargs)
            except ValidationError as exc:
                return _error(
                    "kanban_request_invalid",
                    "request body is invalid",
                    400,
                    {"validation": exc.errors(include_url=False)},
                )
            except KanbanServiceError as exc:
                return _error(exc.code, exc.message, exc.status_code, exc.details)

        return wrapped

    return decorate


@kanban_bp.get("/capabilities")
@check_auth
@_endpoint()
def capabilities():
    return _success(
        KanbanProjectionService().capabilities(_principal(), request.args.get("board_id"))
    )


@kanban_bp.get("/boards")
@check_auth
@_endpoint()
def boards():
    return _success(
        KanbanProjectionService().list_boards(
            _principal(), limit=_limit(), cursor=request.args.get("cursor")
        )
    )


@kanban_bp.post("/boards")
@check_auth
@_endpoint(write=True)
def create_board():
    return _success(
        KanbanProjectionService().create_board(_parse(CreateBoardCommand), _principal()), 201
    )


@kanban_bp.get("/boards/<path:board_id>")
@check_auth
@_endpoint()
def board(board_id: str):
    return _success(KanbanProjectionService().get_board(board_id, _principal()))


@kanban_bp.get("/boards/<path:board_id>/snapshot")
@check_auth
@_endpoint()
def snapshot(board_id: str):
    return _success(
        KanbanProjectionService().get_snapshot(board_id, _principal())
    )


@kanban_bp.get("/boards/<path:board_id>/cards")
@check_auth
@_endpoint()
def cards(board_id: str):
    column_raw = request.args.get("column_id")
    try:
        column = KanbanColumnId(column_raw) if column_raw else None
    except ValueError as exc:
        raise KanbanServiceError(
            "kanban_filter_invalid", "column_id is invalid", status_code=400
        ) from exc
    return _success(
        KanbanProjectionService().list_cards(
            board_id,
            _principal(),
            limit=_limit(),
            cursor=request.args.get("cursor"),
            column_id=column,
            assignee_id=request.args.get("assignee_id"),
            blocked=_bool_filter("blocked"),
            query=request.args.get("q"),
        )
    )


@kanban_bp.post("/boards/<path:board_id>/cards")
@check_auth
@_endpoint(write=True)
def create_card(board_id: str):
    return _success(
        KanbanProjectionService().create_card(
            board_id, _parse(CreateCardCommand), _principal()
        ),
        201,
    )


@kanban_bp.get("/boards/<path:board_id>/cards/<card_id>")
@check_auth
@_endpoint()
def card(board_id: str, card_id: str):
    return _success(KanbanProjectionService().get_card(board_id, card_id, _principal()))


@kanban_bp.get("/boards/<path:board_id>/cards/<card_id>/comments")
@check_auth
@_endpoint()
def comments(board_id: str, card_id: str):
    return _success(
        KanbanProjectionService().list_comments(
            board_id,
            card_id,
            _principal(),
            limit=_limit(),
            cursor=request.args.get("cursor"),
        )
    )


@kanban_bp.get("/boards/<path:board_id>/cards/<card_id>/activity")
@check_auth
@_endpoint()
def activity(board_id: str, card_id: str):
    return _success(
        KanbanProjectionService().list_activity(
            board_id,
            card_id,
            _principal(),
            limit=_limit(),
            cursor=request.args.get("cursor"),
        )
    )


@kanban_bp.get("/boards/<path:board_id>/events")
@check_auth
@_endpoint(rate_limit_namespace=KANBAN_EVENT_RECONNECT)
def events(board_id: str):
    principal = _principal()
    KanbanProjectionService().get_board(board_id, principal)
    after_sequence, limit = _event_cursor()
    return _success(
        get_kanban_event_stream_service().reconnect(
            board_id=board_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    )


def _command(method, model, card_id: str):
    return _success(method(card_id, _parse(model), _principal()))


@kanban_bp.post("/cards/<card_id>/commands/move")
@check_auth
@_endpoint(write=True)
def move(card_id: str):
    return _command(KanbanProjectionService().move_card, MoveCardCommand, card_id)


@kanban_bp.post("/cards/<card_id>/commands/assign")
@check_auth
@_endpoint(write=True)
def assign(card_id: str):
    return _command(KanbanProjectionService().assign_card, AssignCardCommand, card_id)


@kanban_bp.post("/cards/<card_id>/commands/comment")
@check_auth
@_endpoint(write=True)
def comment(card_id: str):
    return _command(KanbanProjectionService().comment_card, CommentCardCommand, card_id)


@kanban_bp.post("/cards/<card_id>/commands/set-dependencies")
@check_auth
@_endpoint(write=True)
def dependencies(card_id: str):
    return _command(
        KanbanProjectionService().set_dependencies, SetDependenciesCommand, card_id
    )


@kanban_bp.post("/cards/<card_id>/commands/block")
@check_auth
@_endpoint(write=True)
def block(card_id: str):
    return _command(KanbanProjectionService().block_card, BlockCardCommand, card_id)


@kanban_bp.post("/cards/<card_id>/commands/complete")
@check_auth
@_endpoint(write=True)
def complete(card_id: str):
    return _command(
        KanbanProjectionService().complete_card, CompleteCardCommand, card_id
    )
