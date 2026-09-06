"""Deterministic Hub response eligibility; no LLM, tool or transport authority."""

import re
from dataclasses import dataclass

from agent.services.meet_chat_contract import ChatEvent, ChatScope, require_integer
from agent.services.meet_contract import MeetError


@dataclass(frozen=True)
class ChatReplyPolicy:
    mode: str = "off"
    mention: str = "ananta"
    max_session_replies: int = 40
    max_room_per_minute: int = 6
    max_sender_per_minute: int = 3
    cooldown_ms: int = 10_000
    max_reply_chars: int = 450
    max_output_tokens: int = 128
    session_output_tokens: int = 5120

    def __post_init__(self):
        if self.mode not in ("off", "mention", "direct_question", "room"):
            raise MeetError("meet_chat_policy_invalid")
        if not isinstance(self.mention, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.mention):
            raise MeetError("meet_chat_mention_invalid")
        for key, minimum, maximum in (
            ("max_session_replies", 1, 1000),
            ("max_room_per_minute", 1, 60),
            ("max_sender_per_minute", 1, 20),
            ("cooldown_ms", 0, 60_000),
            ("max_reply_chars", 1, 450),
            ("max_output_tokens", 1, 128),
            ("session_output_tokens", 1, 128_000),
        ):
            require_integer(getattr(self, key), minimum, maximum)

    def rejection(self, event: ChatEvent, scope: ChatScope, now_ms: int):
        if self.mode == "off":
            return "disabled"
        if (
            event.session_id != scope.session_id
            or event.generation != scope.generation
            or event.room_id != scope.room_id
            or event.membership_epoch != scope.membership_epoch
        ):
            return "scope_mismatch"
        if now_ms >= scope.deadline_ms:
            return "lease_expired"
        if not now_ms - 30_000 <= event.sent_at_ms <= now_ms + 2000:
            return "event_expired"
        if event.sender_peer_id == scope.own_peer_id or event.sender_kind != "human":
            return "self_or_machine_input"
        mentioned = re.search(r"(?<![\w@])@" + re.escape(self.mention) + r"(?![\w-])", event.text, re.IGNORECASE)
        if self.mode in ("mention", "direct_question") and not mentioned:
            return "not_addressed"
        if self.mode == "direct_question" and not event.text.rstrip().endswith("?"):
            return "not_direct_question"
        return None
