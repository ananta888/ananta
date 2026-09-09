"""Operator-owned reservation costs, not a network shaper or free-VRAM claim."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from agent.models.meet_role_assignment import publisher_origin

_PUBLICATION_BPS = {"speech.publish": 128_000, "avatar.publish": 1_200_000, "screen.publish": 2_500_000}
_MAX_RECEIVERS = 19  # Existing maximum room size is twenty including the publisher.


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DialogCapacityPolicy:
    sessions: int = 8
    publisher_sessions: int = 2
    publication_bps: int = 384_000_000

    def __post_init__(self):
        for name, minimum, maximum in (
            ("sessions", 1, 32),
            ("publisher_sessions", 1, 2),
            ("publication_bps", 1, 2_000_000_000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("meet_dialog_capacity_policy_invalid")

    def projection(self):
        return {"schema": "ananta.meet-dialog-capacity-policy.v1", **asdict(self)}

    def fits(self, active, candidate):
        return (
            len(active) < self.sessions
            and sum(row["publisher"] == candidate["publisher"] for row in active) < self.publisher_sessions
            and sum(row["publication_bps"] for row in active) + candidate["publication_bps"] <= self.publication_bps
        )


def reservation(scope, publisher):
    result = {key: getattr(scope, key) for key in ("task_id", "lease_id", "runtime_id", "tenant_id", "project_id")}
    if any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in result.values()):
        raise ValueError("meet_dialog_capacity_binding_invalid")
    deadline = scope.deadline
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("meet_dialog_capacity_deadline_invalid")
    # Reserve all authorized publications even while their independent controls
    # are paused. No future source activation can increase the admitted cost.
    cost = sum(rate for capability, rate in _PUBLICATION_BPS.items() if capability in scope.capabilities)
    return result | {
        "publisher": publisher_origin(publisher),
        "deadline": deadline,
        "publication_bps": cost * _MAX_RECEIVERS,
    }
