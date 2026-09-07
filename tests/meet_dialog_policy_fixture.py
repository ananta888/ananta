"""Explicit synthetic project policy; never replaces actual Hub task authority."""

from agent.services.meet_contract import MeetError, MeetProfile


class SyntheticMeetBinding:
    def __init__(self, origin, room_id, principal):
        self.profile = MeetProfile(origin)
        self.room_id, self.principal = room_id, principal

    def require_write_access(self, actor, project, task=""):
        if actor != self.principal or project != "synthetic":
            raise MeetError("test_scope_denied", 403)

    def read(self, actor, project, task=""):
        self.require_write_access(actor, project, task)
        return {"invite_url": self.profile.invite(self.room_id)}
