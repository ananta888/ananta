"""Collaborators of the AI-Snake execution routes and their single override seam.

The chat and ``/snake/ask`` handlers in :mod:`.snakes_execution_handlers`
reach model generation, worker routing, the chat provider, background replies,
room messages and chat-session ownership only through this bundle, resolved
once per request. Tests replace collaborators per application with
``SNAKE_EXECUTION_DEPENDENCIES.install(app, generate_text=fake)`` (or
``override``) instead of monkeypatching attributes of the handler module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.llm_integration import generate_text
from agent.routes.route_dependency_seam import RouteDependencySeam

from .snakes_chat_helpers import _append_room_ai_message
from .snakes_chat_provider import _resolve_ai_snake_chat_provider
from .snakes_chat_reply_spawner import _spawn_ai_chat_reply
from .snakes_execution_session_helpers import owned_chat_session_snapshot
from .snakes_full_scan import worker_chat_full_scan
from .snakes_worker_routing import (
    _pick_worker_for_ask,
    _resolve_lmstudio_model_for_worker,
    _worker_propose,
)


@dataclass(frozen=True)
class SnakeExecutionDependencies:
    """Services the AI-Snake execution handlers delegate to."""

    generate_text: Callable[..., Any]
    resolve_chat_provider: Callable[..., tuple[str, str | None, str | None]]
    pick_worker_for_ask: Callable[..., Any]
    resolve_lmstudio_model_for_worker: Callable[..., Any]
    worker_propose: Callable[..., tuple[Any, Any]]
    worker_chat_full_scan: Callable[..., tuple[Any, Any]]
    spawn_ai_chat_reply: Callable[..., None]
    append_room_message: Callable[..., Any]
    owned_chat_session_snapshot: Callable[..., Any]


def production_snake_execution_dependencies() -> SnakeExecutionDependencies:
    return SnakeExecutionDependencies(
        generate_text=generate_text,
        resolve_chat_provider=_resolve_ai_snake_chat_provider,
        pick_worker_for_ask=_pick_worker_for_ask,
        resolve_lmstudio_model_for_worker=_resolve_lmstudio_model_for_worker,
        worker_propose=_worker_propose,
        worker_chat_full_scan=worker_chat_full_scan,
        spawn_ai_chat_reply=_spawn_ai_chat_reply,
        append_room_message=_append_room_ai_message,
        owned_chat_session_snapshot=owned_chat_session_snapshot,
    )


SNAKE_EXECUTION_DEPENDENCIES: RouteDependencySeam[SnakeExecutionDependencies] = RouteDependencySeam(
    "ananta.snake_execution_dependencies",
    production_snake_execution_dependencies,
)
