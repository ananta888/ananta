"""Hub-only admission foundation, deliberately not exposed as an HTTP ingress."""

import time
from typing import Protocol

from agent.models.meet_chat_admission import (  # noqa: F401 -- ChatReservation re-exported
    AuthorizedChatSession,
    ChatAdmission,
    ChatReservation,
)
from agent.models.meet_chat_contract import ChatEvent


class ChatAuthorityPort(Protocol):
    def current(self, session_id: str) -> AuthorizedChatSession | None:
        """Revalidate Hub task/lease/project AND acknowledged Meet read/send rights.

        Authenticate the delivering runtime independently; never derive this
        projection or sender classification from caller assertions. No grant
        means None. This port is not implemented by the legacy v1 publisher.
        """
        ...


class ChatReservationPort(Protocol):
    def reserve(self, session: AuthorizedChatSession, event: ChatEvent, now_ms: int) -> ChatAdmission: ...


class MeetChatAdmissionService:
    def __init__(self, authority: ChatAuthorityPort, reservations: ChatReservationPort, clock=time.time):
        self.authority, self.reservations, self.clock = authority, reservations, clock

    def admit(self, raw: bytes) -> ChatAdmission:
        event = ChatEvent.parse(raw)
        session = self.authority.current(event.session_id)
        if session is None:
            return ChatAdmission("not_authorized")
        rejection = session.policy.rejection(event, session.scope, int(self.clock() * 1000))
        if rejection:
            return ChatAdmission(rejection)
        outcome = self.reservations.reserve(session, event, int(self.clock() * 1000))
        if outcome.reservation is None:
            return outcome
        # A concurrent revoke/renewal burns the reservation, never retries a
        # possibly dispatched intent. No text is disclosed under the old scope.
        if self.authority.current(event.session_id) != session or int(self.clock() * 1000) >= session.scope.deadline_ms:
            return ChatAdmission("authority_changed")
        return ChatAdmission("reserved", outcome.reservation, event.text)
