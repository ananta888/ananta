"""Compatibility import path.

The implementation lives in ``agent.models.meet_chat_policy``: a dependency-free contract that repositories
may import (agent.models).
"""

from agent.models.meet_chat_policy import (  # noqa: F401
    ChatReplyPolicy,
)
