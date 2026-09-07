"""Prepare a Hub-owned room binding; never claim Meet membership or create media."""

import secrets
from typing import Callable, Protocol

from agent.services.meet_contract import MeetError


class RoomBindingPort(Protocol):
    def require_write_access(self, principal, project, task=""): ...
    def read(self, principal, project, task="") -> dict: ...
    def change(self, principal, project, task, payload) -> dict: ...


def random_room():
    return "room-" + secrets.token_hex(9)


class MeetRoomAllocation:
    def __init__(
        self, binding: RoomBindingPort, allowed_scopes, *, invite: Callable[[str], str], room_factory=random_room
    ):
        self.binding, self.allowed_scopes = binding, frozenset(allowed_scopes)
        self.invite, self.room_factory = invite, room_factory

    def allocate(self, principal, project, task, payload):
        self.binding.require_write_access(principal, project, task)
        if (principal.tenant_id, project) not in self.allowed_scopes:
            raise MeetError("meet_room_allocation_denied", 403)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"expected_revision"}
            or type(payload["expected_revision"]) is not int
            or not 0 <= payload["expected_revision"] < 2_147_483_647
        ):
            raise MeetError("meet_room_allocation_payload_invalid")
        current = self.binding.read(principal, project, task)
        if current["revision"] != payload["expected_revision"]:
            raise MeetError("meet_binding_conflict", 409)
        if current["invite_url"] is not None:
            return current
        try:
            invitation = self.invite(self.room_factory())
        except (ValueError, TypeError, OSError):
            raise MeetError("meet_room_allocation_unavailable", 503) from None
        # Existing write path reauthorizes and atomically compares the captured
        # revision. A losing race must not overwrite an intervening association.
        return self.binding.change(
            principal, project, task, {"expected_revision": current["revision"], "invite_url": invitation}
        )
