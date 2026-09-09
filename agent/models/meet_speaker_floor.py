"""Immutable Hub speech-resource bindings; never evidence or execution authority."""

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from ananta_contracts.meet_dialog import ID
from ananta_contracts.meet_spoken_reply import validate_spoken_binding

WAIT_MS = 10_000
OUTPUT_MS = 60_000  # Bounded source opening plus at most fifty seconds of playback.
CLEANUP_MS = 4_000
AGING_MS = 3_000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class SpeakerTurn:
    """Construct only from current Hub policy, never from Worker-selected priority."""

    origin: str
    binding_json: str
    turn_id: str
    priority: int = 0

    def __post_init__(self):
        if not isinstance(self.origin, str) or not isinstance(self.binding_json, str) or len(self.binding_json) > 8192:
            raise ValueError("meet_speaker_turn_invalid")
        url = urlsplit(self.origin)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.netloc != url.hostname
            or url.path
            or url.query
            or url.fragment
            or len(self.origin) > 260
            or not isinstance(self.turn_id, str)
            or not ID.fullmatch(self.turn_id)
            or type(self.priority) is not int
            or not 0 <= self.priority <= 2
        ):
            raise ValueError("meet_speaker_turn_invalid")
        binding = validate_spoken_binding(json.loads(self.binding_json))
        if self.binding_json != json.dumps(binding, sort_keys=True, separators=(",", ":")):
            raise ValueError("meet_speaker_turn_invalid")

    @classmethod
    def from_binding(cls, origin, binding, turn_id, *, priority=0):
        return cls(origin, json.dumps(binding, sort_keys=True, separators=(",", ":")), turn_id, priority)

    @property
    def binding(self):
        return json.loads(self.binding_json)  # Defensive copy: callers cannot mutate a stored scope.

    @property
    def room_key(self):
        # The physical room is shared even if eligible tasks have distinct tenants.
        # Separate tenant pools would incorrectly admit simultaneous speakers.
        return _digest(["meet-speaker-room-v1", self.origin, self.binding["room_id"]])

    @property
    def identity(self):
        # Exclude policy priority: changing it must not replay the same input turn.
        return _digest(["meet-speaker-turn-v1", self.origin, self.binding_json, self.turn_id])

    @property
    def deadline_ms(self):
        return self.binding["deadline_ms"]

    @property
    def metadata(self):
        binding = self.binding
        return {
            **{name: binding[name] for name in ("tenant_id", "project_id", "task_id", "lease_id", "runtime_id")},
            "authority_digest": _digest([self.origin, self.binding_json, self.turn_id]),
            "deadline_ms": self.deadline_ms,
            "priority": self.priority,
        }


def require_clock(now_ms):
    if type(now_ms) is not int or not 0 < now_ms < 2**53:
        raise ValueError("meet_speaker_clock_invalid")
