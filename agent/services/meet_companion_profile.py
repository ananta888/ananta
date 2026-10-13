"""Hub admission of the closed additive Companion execution profile."""

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_profile import validate_companion_profile


def bound_companion_profile(value, *, enabled=True):
    try:
        selected = validate_companion_profile(value)
    except (ValueError, TypeError):
        raise MeetError("meet_companion_execution_profile_invalid", 403) from None
    if selected and (not enabled or value.get("chat_mode") == "off"):
        raise MeetError("meet_companion_execution_not_enabled", 403)
    return selected


def companion_execution_fields(selected):
    return {"companion_execution": True} if selected else {}
