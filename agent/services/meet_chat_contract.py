"""Compatibility import path.

The implementation lives in ``agent.models.meet_chat_contract``: a dependency-free contract that repositories
may import (agent.models).
"""

from agent.models.meet_chat_contract import (  # noqa: F401
    CHAT_SCHEMA,
    IDENTIFIER,
    MAX_EVENT_BYTES,
    ChatEvent,
    ChatScope,
    require_identifier,
    require_integer,
)
