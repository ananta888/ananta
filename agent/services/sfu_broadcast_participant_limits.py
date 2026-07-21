"""Hard participant and fanout caps for SFU broadcast paths.

The values are explicit and intentionally small by default. They represent the
known legacy hard limits while we keep a single Hub-owned source of truth for
receiver, group, and publication fanout caps.
"""

from __future__ import annotations

SFU_BROADCAST_MAX_GROUP_MEMBERS = 8
SFU_BROADCAST_MAX_PUBLICATION_RECIPIENTS = SFU_BROADCAST_MAX_GROUP_MEMBERS - 1
SFU_BROADCAST_MAX_ROOM_PARTICIPANTS = SFU_BROADCAST_MAX_GROUP_MEMBERS
SFU_BROADCAST_MAX_DATA_DESTINATIONS = 7

__all__ = [
    "SFU_BROADCAST_MAX_DATA_DESTINATIONS",
    "SFU_BROADCAST_MAX_GROUP_MEMBERS",
    "SFU_BROADCAST_MAX_PUBLICATION_RECIPIENTS",
    "SFU_BROADCAST_MAX_ROOM_PARTICIPANTS",
]
