"""Trace inspection endpoints for owned snake chat sessions."""

from __future__ import annotations

import logging

from flask import jsonify, request

from .snakes_state import (
    _optional_user_auth,
    _snake_bound_to_auth,
    _snakes,
    snakes_bp,
)


def trace_owned_snake(snake_id: str):
    auth = _optional_user_auth()
    if not auth:
        return None, (jsonify({"error": "user_authentication_required"}), 401)
    snake = _snakes.get(snake_id)
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return None, (
            jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}),
            404,
        )
    return snake, None


@snakes_bp.route("/snakes/<snake_id>/chat/traces", methods=["GET"])
def chat_traces_list(snake_id: str):
    """List traces owned by the requested snake."""

    _snake, error = trace_owned_snake(snake_id)
    if error is not None:
        return error
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store

        store = get_trace_store()
        limit = min(int(request.args.get("limit") or 20), 100)
        traces = store.list_traces(snake_id=snake_id, limit=limit)
        return jsonify({"traces": traces, "snake_id": snake_id}), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_traces_list failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500


@snakes_bp.route("/snakes/<snake_id>/chat/traces/<trace_id>", methods=["GET"])
def chat_trace_detail(snake_id: str, trace_id: str):
    """Return trace metadata owned by the requested snake."""

    _snake, error = trace_owned_snake(snake_id)
    if error is not None:
        return error
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store

        store = get_trace_store()
        trace = store.get_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Trace nicht gefunden"}), 404
        if trace.get("snake_id") and trace["snake_id"] != snake_id:
            return jsonify({"error": "trace_not_found", "error_code": "trace_not_found"}), 404
        return jsonify({"trace": trace}), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_trace_detail failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500


@snakes_bp.route("/snakes/<snake_id>/chat/traces/<trace_id>/events", methods=["GET"])
def chat_trace_events(snake_id: str, trace_id: str):
    """Return trace events after the requested sequence number."""

    _snake, error = trace_owned_snake(snake_id)
    if error is not None:
        return error
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store

        store = get_trace_store()
        trace = store.get_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Trace nicht gefunden"}), 404
        if trace.get("snake_id") and trace["snake_id"] != snake_id:
            return jsonify({"error": "trace_not_found", "error_code": "trace_not_found"}), 404
        since_seq = max(0, int(request.args.get("since_seq") or 0))
        events = store.get_events(trace_id, since_seq=since_seq)
        return jsonify(
            {
                "trace_id": trace_id,
                "current_status": trace.get("status", "unknown"),
                "latest_seq": trace.get("latest_seq", -1),
                "events": events,
            }
        ), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_trace_events failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500
