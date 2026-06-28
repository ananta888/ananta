"""T03.01: Snake-Registrierung und Chat-API über Hub.

Endpunkte:
  POST   /snakes                          – Snake registrieren
  GET    /snakes                          – alle aktiven Snakes auflisten
  DELETE /snakes/<id>                     – Snake abmelden
  POST   /snakes/<id>/messages            – Legacy: einfache Nachricht an Snake
  GET    /snakes/<id>/messages            – Legacy: Nachrichten abrufen
  POST   /snakes/<id>/heartbeat          – Liveness-Ping
  POST   /snakes/<id>/chat/messages      – ChatMessage-v1 senden
  GET    /snakes/<id>/chat/messages      – Chat-Nachrichten abrufen (cursor)
  POST   /snakes/<id>/chat/ack           – Gelesene Nachrichten bestätigen
  GET    /snakes/participants            – Teilnehmerliste mit Status
  POST   /snake/ask                      – Synchrone AI-Antwort (TUI worker mode)
  POST   /worker-context                 – WorkerContextHandoffV3 mit CandidateFiles (CWFH-009)
"""
from __future__ import annotations

from .snakes_state import (  # re-export the historical route-state API
    _MAX_CHAT_MSGS, _MAX_ROOM_MSGS, _MAX_SNAKES, _SCAN_CANCELS,
    _VALID_CHANNEL_TYPES, _VALID_COLORS, _VALID_ROLES, _VALID_VISIBILITY,
    _chat_messages, _is_local_request, _messages, _next_free_color,
    _optional_user_auth, _request_device_id, _room_messages,
    _snake_bound_to_auth, _snakes, snakes_bp,
)


# Register routes from sub-modules
from .snakes_config_routes import *  # noqa: F401, F403, E402
from .snakes_execution_routes import *  # noqa: F401, F403, E402
# Re-export private helpers needed by external importers / monkeypatches
from .snakes_execution_routes import (  # noqa: F401, E402
    _spawn_ai_chat_reply,
    _worker_chat_full_scan,
    _pick_worker_for_ask,
    _build_grounded_snake_prompt,
    _snake_retrieval_dry_run,
)
