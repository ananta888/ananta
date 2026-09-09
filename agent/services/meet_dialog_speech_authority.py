"""Add independent current speech policy to ordinary Hub chat admission."""

from dataclasses import replace

from agent.services.meet_chat_admission import AuthorizedChatSession
from agent.services.meet_dialog_controls import chat_policy_revision


class CurrentDialogSpeechAuthority:
    def __init__(
        self, authority, identifiers, chat, input_sent_at_ms, *, voices=None, voice_projection=None, floor_required=None
    ):
        self.authority, self.identifiers, self.chat = authority, identifiers, chat
        self.input_sent_at_ms = input_sent_at_ms
        self.voices, self.voice_projection = voices, voice_projection
        self.floor_required = floor_required

    def current(self, session_id):
        scope = self.authority.current(*self.identifiers)
        if self.floor_required is not None and scope.speaker_floor is not self.floor_required:
            return None
        speech = scope.controls.speech
        if (
            speech is None
            or not speech.enabled
            or "speech.publish" not in scope.capabilities
            or self.input_sent_at_ms < max(speech.since, scope.controls.chat.since)
        ):
            return None
        if (scope.voice_selection is None) != (self.voice_projection is None):
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
        if self.voice_projection is not None:
            if self.voices is None:
                return None
            try:
                self.voices.require_current(scope, self.voice_projection)
            except (ValueError, PermissionError):
                return None
        # Network and policy reads may overlap an independent source CAS. Only
        # avatar/audio/screen changes may be ignored, never speech/chat/identity.
        fresh = self.authority.current(*self.identifiers)
        if (
            fresh.controls.speech != scope.controls.speech
            or fresh.controls.chat != scope.controls.chat
            or fresh != replace(scope, controls=fresh.controls, avatar_selection=fresh.avatar_selection)
        ):
            return None
        revision = chat_policy_revision(chat.scope.policy_revision, speech.revision)
        return AuthorizedChatSession(replace(chat.scope, policy_revision=revision), chat.policy)


def spoken_binding(reservation, meet_session_id, *, voice_projection=None):
    from ananta_contracts.meet_dialog_voice import voice_binding_fields

    scope = reservation.scope
    normal_revision, speech_revision = divmod(scope.policy_revision, 1024)
    receive_revision, chat_revision = divmod(normal_revision, 1024)
    return {
        **(voice_binding_fields(voice_projection) if voice_projection is not None else {}),
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
