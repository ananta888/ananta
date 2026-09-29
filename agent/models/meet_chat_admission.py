"""Value types of a Meet chat admission: the authorized session, a reservation and the outcome."""

from dataclasses import dataclass, field

from agent.models.meet_chat_contract import ChatScope
from agent.models.meet_chat_policy import ChatReplyPolicy


@dataclass(frozen=True)
class AuthorizedChatSession:
    scope: ChatScope
    policy: ChatReplyPolicy


@dataclass(frozen=True)
class ChatReservation:
    intent_id: str
    scope: ChatScope
    message_id: str
    sender_peer_id: str
    max_reply_chars: int
    max_output_tokens: int


@dataclass(frozen=True)
class ChatAdmission:
    code: str
    reservation: ChatReservation | None = None
    # Volatile untrusted user input only, never audit metadata or a system prompt.
    text: str | None = field(default=None, repr=False)
