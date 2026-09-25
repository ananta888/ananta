"""Bind a project to the room server's public directory entry.

Creating the directory entry uses an operator identity; writing it into the Meet
binding stays on the ordinary authorized change path with a revision check. Two
callers reach it: an operator with a Hub session, and the worker-key companion
self-heal (``/internal/public-room``). The latter can only repoint a project it
is media-scoped for, and only at that project's own operator-titled public entry
on the configured origin -- never at an arbitrary room.
"""

from agent.services.meet_contract import MeetError
from agent.services.meet_room_allocation import RoomBindingPort


class MeetPublicRoomPublication:
    def __init__(self, binding: RoomBindingPort, rooms, *, allowed_scopes=None):
        self.binding, self.rooms = binding, rooms
        self.allowed_scopes = None if allowed_scopes is None else frozenset(allowed_scopes)

    def publish(self, principal, project, task=""):
        self.binding.require_write_access(principal, project, task)
        if self.allowed_scopes is not None and (principal.tenant_id, project) not in self.allowed_scopes:
            raise MeetError("meet_public_room_denied", 403)
        current = self.binding.read(principal, project, task)
        room = self.rooms.ensure_public_room(project, task)
        bound = current
        if current["invite_url"] != room.invite_url:
            # Existing write path reauthorizes and compares the captured
            # revision, so a losing race never overwrites a newer association.
            bound = self.binding.change(
                principal, project, task, {"expected_revision": current["revision"], "invite_url": room.invite_url}
            )
        return {
            "schema": "ananta.meet-public-room.v1",
            "roomId": room.room_id,
            "inviteUrl": room.invite_url,
            "visibility": room.visibility,
            "reused": room.reused,
            "revision": bound["revision"],
            "project_id": project,
            "task_id": task or None,
        }
