"""Add independent current speech policy to ordinary Hub chat admission."""

from dataclasses import replace

from agent.services.meet_chat_admission import AuthorizedChatSession
from agent.services.meet_dialog_controls import chat_policy_revision


class CurrentDialogSpeechAuthority:
    def __init__(self, authority, identifiers, chat, input_sent_at_ms):
        self.authority, self.identifiers, self.chat = authority, identifiers, chat
        self.input_sent_at_ms = input_sent_at_ms

    def current(self, session_id):
        scope = self.authority.current(*self.identifiers)
        speech = scope.controls.speech
        if (
            speech is None
            or not speech.enabled
            or "speech.publish" not in scope.capabilities
            or self.input_sent_at_ms < max(speech.since, scope.controls.chat.since)
        ):
            return None
        chat = self.chat.current(session_id)
        if (
            chat is None
            or chat.policy.mode != scope.chat_mode
            or chat.scope.policy_revision % 1024 != scope.controls.chat.revision
            or any(
                getattr(chat.scope, name) != getattr(scope, name)
                for name in (
                    "origin",
                    "tenant_id",
                    "project_id",
                    "task_id",
                    "lease_id",
                    "runtime_id",
                    "session_id",
                    "room_id",
                )
            )
        ):
            return None
        revision = chat_policy_revision(chat.scope.policy_revision, speech.revision)
        return AuthorizedChatSession(replace(chat.scope, policy_revision=revision), chat.policy)


def spoken_binding(reservation, meet_session_id):
    scope = reservation.scope
    normal_revision, speech_revision = divmod(scope.policy_revision, 1024)
    receive_revision, chat_revision = divmod(normal_revision, 1024)
    return {
        **{
            name: getattr(scope, name)
            for name in (
                "tenant_id",
                "project_id",
                "task_id",
                "lease_id",
                "runtime_id",
                "session_id",
                "room_id",
                "own_peer_id",
                "generation",
                "membership_epoch",
                "deadline_ms",
            )
        },
        "sender_peer_id": reservation.sender_peer_id,
        "meet_session_id": meet_session_id,
        "receive_revision": receive_revision,
        "chat_revision": chat_revision,
        "speech_revision": speech_revision,
    }
