"""Selection changes advance only avatar authority and never toggle a source."""

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import controls_projection


def advance_avatar_selection_controls(scope, since):
    old = scope.controls.avatar
    if (
        old is None
        or "avatar.publish" not in scope.capabilities
        or scope.controls.revision >= 1023
        or type(since) is not int
        or not old.since <= since < 2**53
    ):
        raise MeetError("meet_dialog_avatar_controls_conflict", 409)
    controls = controls_projection(scope.controls)
    controls["revision"] += 1
    controls["avatar"] = {"enabled": old.enabled, "revision": old.revision + 1, "since": since}
    return controls
