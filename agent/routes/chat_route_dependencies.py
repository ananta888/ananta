"""Collaborators of the chat routes (``/api/chat``) and their single override seam.

The chat handlers use exactly these collaborators; everything else they need is
pure request/response logic. Handlers obtain them via
:func:`chat_route_dependencies`; tests replace them per application with
``CHAT_ROUTE_DEPENDENCIES.override(app, get_manager=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.services.chat_process_binding import (
    resolve_effective_process,
    runtime_overlay,
    signal_session_gate,
    start_session_process,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager


@dataclass(frozen=True)
class ChatRouteDependencies:
    """Services the chat routes delegate to."""

    get_manager: Callable[[], Any]
    resolve_effective_process: Callable[..., Any]
    start_session_process: Callable[..., Any]
    runtime_overlay: Callable[..., Any]
    signal_session_gate: Callable[..., Any]


def production_chat_route_dependencies() -> ChatRouteDependencies:
    return ChatRouteDependencies(
        get_manager=get_manager,
        resolve_effective_process=resolve_effective_process,
        start_session_process=start_session_process,
        runtime_overlay=runtime_overlay,
        signal_session_gate=signal_session_gate,
    )


CHAT_ROUTE_DEPENDENCIES: RouteDependencySeam[ChatRouteDependencies] = RouteDependencySeam(
    "ananta.chat_route_dependencies",
    production_chat_route_dependencies,
)


def chat_route_dependencies() -> ChatRouteDependencies:
    """The chat collaborators of the current application."""

    return CHAT_ROUTE_DEPENDENCIES.resolve()
