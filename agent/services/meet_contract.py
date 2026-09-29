"""Compatibility import path.

The implementation lives in ``agent.models.meet_contract``: a dependency-free contract that repositories may
import (agent.models).
"""

from agent.models.meet_contract import (  # noqa: F401
    MeetError,
    MeetingBinding,
    MeetingStore,
    MeetProfile,
)
