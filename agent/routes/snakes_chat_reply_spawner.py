"""Background AI-Snake chat replies: the UI-state store, the thread gate and the runner.

The reply runner receives its collaborators explicitly at composition time;
nothing is looked up in a route module at call time. Route handlers reach
``spawn_ai_chat_reply`` through
:data:`agent.routes.snakes_execution_dependencies.SNAKE_EXECUTION_DEPENDENCIES`.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings
from agent.llm_integration import generate_text

from .snakes_chat_helpers import _append_room_ai_message
from .snakes_chat_provider import _resolve_ai_snake_chat_provider
from .snakes_chat_reply_runner import SnakeChatReplyRunner
from .snakes_worker_routing import _pick_worker_for_ask, _worker_propose

# In-memory UI state pushed by the browser via PUT /snakes/<id>/ui-state.
# Keyed by snake_id; used to enrich LLM prompts with current navigation context.
_snake_ui_state: dict[str, dict] = {}


def _background_threads_disabled() -> bool:
    return bool(
        (has_app_context() and bool(getattr(current_app, "testing", False)))
        or str(getattr(settings, "role", "")).strip().lower() == "test"
        or os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


_chat_reply_runner = SnakeChatReplyRunner(
    ui_state=_snake_ui_state,
    resolve_chat_provider=_resolve_ai_snake_chat_provider,
    append_room_message=_append_room_ai_message,
    worker_propose=_worker_propose,
    worker_picker=_pick_worker_for_ask,
    generate_text=generate_text,
    logger=logging.getLogger("agent.routes.snakes_execution_handlers"),
)


def _spawn_ai_chat_reply(
    *,
    user_text: str,
    snake_id: str | None = None,
    ui_context: dict | None = None,
    client_session_id: str = "",
    context_history: list[dict[str, str]] | None = None,
    session_snapshot: dict[str, Any] | None = None,
    owner_principal: dict[str, str] | None = None,
) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        _chat_reply_runner.run(
            prompt=prompt,
            snake_id=snake_id,
            ui_context=ui_context,
            client_session_id=client_session_id,
            context_history=context_history,
            session_snapshot=session_snapshot,
            owner_principal=owner_principal,
        )

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()
