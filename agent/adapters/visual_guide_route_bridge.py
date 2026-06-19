"""Route-owned integration bridge for VisualGuide services."""

from __future__ import annotations

from typing import Any


def background_threads_disabled() -> bool:
    from agent.routes.snakes_execution_routes import _background_threads_disabled

    return _background_threads_disabled()


def visual_session_settings() -> dict:
    from agent.routes.snakes_execution_routes import _visual_session_settings

    return _visual_session_settings()


def append_room_ai_message(**kwargs: Any) -> None:
    from agent.routes.snakes_execution_routes import _append_room_ai_message

    _append_room_ai_message(**kwargs)


def broadcast_snake_event(snake_id: str, event_type: str, payload: dict[str, Any]) -> None:
    from agent.routes.snake_event_broadcaster import broadcast_snake_event

    broadcast_snake_event(snake_id, event_type, payload)


def current_ai_snake_config() -> dict:
    from agent.routes.ai_snake_config import _current_config

    return _current_config()


def get_trace_store() -> Any:
    from agent.routes.ai_snake_trace_store import get_trace_store as _get_trace_store

    return _get_trace_store()


def trace_recorder(store: Any, trace_id: str) -> Any:
    from agent.routes.ai_snake_trace_store import TraceRecorder

    return TraceRecorder(store, trace_id)
