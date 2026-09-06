"""Proposed MDS chat ingress projection; parsing grants no receive authority."""

import json
import re
from dataclasses import dataclass, field

from agent.services.meet_contract import MeetError, MeetProfile

CHAT_SCHEMA = "ananta.meet-chat-event.draft1"
MAX_EVENT_BYTES = 8192
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,160}")


def require_identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise MeetError("meet_chat_identifier_invalid")


def require_integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MeetError("meet_chat_integer_invalid")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MeetError("meet_chat_duplicate_field")
        result[key] = value
    return result


@dataclass(frozen=True)
class ChatEvent:
    session_id: str
    generation: int
    room_id: str
    membership_epoch: int
    message_id: str
    sender_peer_id: str
    sender_kind: str
    sent_at_ms: int
    text: str = field(repr=False)

    @classmethod
    def parse(cls, raw: bytes):
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_EVENT_BYTES:
            raise MeetError("meet_chat_event_size", 413)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError, RecursionError):
            raise MeetError("meet_chat_event_invalid") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", *cls.__dataclass_fields__}
            or value["schema"] != CHAT_SCHEMA
        ):
            raise MeetError("meet_chat_event_contract_invalid")
        for key in ("session_id", "message_id", "sender_peer_id"):
            require_identifier(value[key])
        for key in ("generation", "membership_epoch", "sent_at_ms"):
            require_integer(value[key], 1, 2**53 - 1)
        if not isinstance(value["room_id"], str) or not re.fullmatch(r"room-[a-f0-9]{18}", value["room_id"]):
            raise MeetError("meet_chat_room_invalid")
        if value["sender_kind"] not in ("human", "machine", "unknown"):
            raise MeetError("meet_chat_sender_invalid")
        text = value["text"]
        try:
            valid_text = (
                isinstance(text, str)
                and bool(text.strip())
                and len(text) <= 2000
                and len(text.encode("utf-8")) <= 4000
                and not any(ord(char) < 32 and char not in "\n\t" for char in text)
            )
        except UnicodeError:
            valid_text = False
        if not valid_text:
            raise MeetError("meet_chat_text_invalid")
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ChatScope:
    """Immutable projection from Hub authority, never accepted from an event."""

    origin: str
    tenant_id: str
    project_id: str
    task_id: str
    session_id: str
    runtime_id: str
    lease_id: str
    generation: int
    room_id: str
    membership_epoch: int
    policy_revision: int
    own_peer_id: str
    deadline_ms: int

    def __post_init__(self):
        profile = MeetProfile(self.origin)
        profile.invite(self.room_id)
        for key in ("tenant_id", "project_id", "task_id", "session_id", "runtime_id", "lease_id", "own_peer_id"):
            require_identifier(getattr(self, key))
        for key in ("generation", "membership_epoch", "policy_revision", "deadline_ms"):
            require_integer(getattr(self, key), 1, 2**53 - 1)
