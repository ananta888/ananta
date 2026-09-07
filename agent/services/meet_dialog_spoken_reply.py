"""Project one admitted Hub child result, without storing media or publishing it."""

import json
import time

from agent.services.meet_chat_admission import MeetChatAdmissionService
from agent.services.meet_chat_contract import ChatEvent
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_chat_authority import CurrentDialogChatAuthority
from agent.services.meet_dialog_speech_authority import CurrentDialogSpeechAuthority, spoken_binding
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.meet_spoken_reply import RESPONSE_SCHEMA, decode_spoken_response, validate_spoken_request


class MeetDialogSpokenReply:
    def __init__(self, authority, meet, reservations, replies, *, clock=time.time, voices=None):
        self.authority, self.meet, self.reservations, self.replies, self.clock = (
            authority,
            meet,
            reservations,
            replies,
            clock,
        )
        self.voices = voices

    def execute(self, payload):
        try:
            validate_spoken_request(payload, self.clock())
        except ValueError:
            raise MeetError("meet_spoken_callback_invalid", 400) from None
        identifiers = tuple(payload[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*identifiers)
        try:
            raw = json.dumps(payload["event"], ensure_ascii=False).encode()
        except UnicodeError:
            raise MeetError("meet_chat_event_invalid", 400) from None
        event = ChatEvent.parse(raw)
        chat = CurrentDialogChatAuthority(
            self.authority, self.meet, identifiers, payload["meet_session_id"], event.sender_peer_id
        )
        projection = None
        if scope.voice_selection is not None:
            if self.voices is None:
                raise MeetError("meet_dialog_voice_profiles_unavailable", 409)
            projection = self.voices.projection(scope)
        current = CurrentDialogSpeechAuthority(
            self.authority, identifiers, chat, event.sent_at_ms, voices=self.voices, voice_projection=projection
        )
        admission = MeetChatAdmissionService(current, self.reservations, clock=self.clock).admit(raw)
        response = {"schema": RESPONSE_SCHEMA, "nonce": payload["nonce"], "code": admission.code, "reply": None}
        if admission.reservation is None:
            return response
        principal = HubSourcePrincipal(scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"}))
        result = (
            self.replies.execute(
                current,
                principal,
                admission,
                speech_profile=projection["profile"],
                voice_selection=scope.voice_selection,
            )
            if projection is not None
            else self.replies.execute(current, principal, admission)
        )
        active = current.current(scope.session_id)
        if active is None or active.scope != admission.reservation.scope:
            raise MeetError("meet_spoken_authority_changed", 409)
        try:
            if (
                result["schema"] != "ananta.meet-chat-reply.v1"
                or result["published"] is not False
                or result["intent_id"] != admission.reservation.intent_id
                or result["reply_to"] != event.message_id
                or result["session_id"] != scope.session_id
                or result["generation"] != admission.reservation.scope.generation
            ):
                raise ValueError()
            media = result["media"]
            binding = spoken_binding(admission.reservation, payload["meet_session_id"], voice_projection=projection)
            response.update(
                code="generated",
                reply={
                    "message_id": result["reply_to"],
                    "text": media["text"],
                    "child_task_id": media["task_id"],
                    "child_lease_id": media["lease_id"],
                    "binding": binding,
                    **{name: media[name] for name in ("audio", "speech", "duration_seconds")},
                },
            )
            decode_spoken_response(response, payload, binding, int(self.clock() * 1000))
        except (ValueError, TypeError, KeyError):
            raise MeetError("meet_spoken_result_invalid", 502) from None
        # Validation can be nontrivial; release only under the still-current source.
        active = current.current(scope.session_id)
        if active is None or active.scope != admission.reservation.scope:
            raise MeetError("meet_spoken_authority_changed", 409)
        return response
