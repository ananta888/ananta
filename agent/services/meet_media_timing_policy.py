"""Hub-selected fixed quality profile, independent of publication permission."""

from agent.services.meet_contract import MeetError


def negotiated_media_timing(enabled):
    if type(enabled) is not bool:
        raise ValueError("meet_media_timing_negotiation_invalid")
    return {"media_timing": True} if enabled else {}


def require_media_timing_mode(scope, enabled):
    if scope.media_timing is not enabled:
        raise MeetError("meet_media_timing_mode_changed", 409)


def validate_media_timing_negotiation(context):
    if "media_timing" in context and context["media_timing"] is not True:
        raise MeetError("meet_media_timing_negotiation_invalid", 403)
