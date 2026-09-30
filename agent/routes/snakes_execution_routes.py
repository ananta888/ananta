"""Public compatibility facade for the snake execution routes.

Importing this module registers the execution routes on ``snakes_bp`` (the
implementation lives in :mod:`.snakes_execution_handlers`). It re-exports the
public names plus the historical private helpers other modules import from
here. It is an ordinary module, not an alias of the implementation: handlers
receive their collaborators through
:data:`.snakes_execution_dependencies.SNAKE_EXECUTION_DEPENDENCIES`.
"""
from __future__ import annotations

from .snakes_execution_handlers import *  # noqa: F401, F403
from .snakes_execution_handlers import (  # noqa: F401
    _VISUAL_GUIDE_EXECUTOR,
    _append_room_ai_message,
    _background_threads_disabled,
    _build_grounded_snake_prompt,
    _build_room_conversation_history,
    _fit_answer_to_chars,
    _pick_worker_for_ask,
    _resolve_ai_snake_chat_provider,
    _should_include_light_ui_context,
    _snake_retrieval_dry_run,
    _snake_ui_state,
    _spawn_ai_chat_reply,
    _spawn_visual_reply,
    _visual_session_log_deltas_only,
    _visual_session_settings,
    _worker_chat_full_scan,
)
