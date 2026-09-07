"""Current chat authority projection, shared by text and spoken replies."""

from agent.services.meet_chat_admission import AuthorizedChatSession
from agent.services.meet_chat_contract import ChatScope
from agent.services.meet_chat_policy import ChatReplyPolicy
from agent.services.meet_dialog_controls import chat_policy_revision


class CurrentDialogChatAuthority:
    def __init__(self, authority, meet, identifiers, meet_session_id, sender_peer_id):
        self.authority, self.meet, self.identifiers = authority, meet, identifiers
        self.meet_session_id, self.sender_peer_id = meet_session_id, sender_peer_id

    def current(self, session_id):
        scope = self.authority.current(*self.identifiers)
        if (
            session_id != scope.session_id
            or not scope.controls.chat.enabled
            or not {"chat.read", "chat.send"} <= set(scope.capabilities)
        ):
            return None
        receipt = self.meet.inspect(*self.identifiers, self.meet_session_id)
        grants = [g for g in receipt["grants"] if g["chatRead"]]
        if not any(g["publisherPeerId"] == self.sender_peer_id for g in grants):
            return None
        lease = receipt["lease"]
        return AuthorizedChatSession(
            ChatScope(
                origin=scope.origin,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                task_id=scope.task_id,
                session_id=scope.session_id,
                runtime_id=scope.runtime_id,
                lease_id=scope.lease_id,
                generation=lease["generation"],
                room_id=scope.room_id,
                membership_epoch=receipt["membershipEpoch"],
                policy_revision=chat_policy_revision(receipt["receiveRevision"], scope.controls.chat.revision),
                own_peer_id=receipt["peerId"],
                deadline_ms=min(lease["expiresAt"], scope.deadline * 1000, *(g["expiresAt"] for g in grants)),
            ),
            ChatReplyPolicy(mode=scope.chat_mode),
        )
