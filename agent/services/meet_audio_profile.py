"""Hub error mapping for an optional immutable receive execution profile."""

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_audio_profile import parse_audio_profile


def bound_audio_profile(value, audio_mode):
    if "audio_profile" not in value:
        return None
    try:
        profile = parse_audio_profile(value["audio_profile"])
        if audio_mode == "off":
            raise ValueError()
        return profile
    except ValueError:
        raise MeetError("meet_audio_profile_invalid", 403) from None


def audio_profile_fields(profile):
    return {} if profile is None else {"audio_profile": profile.projection()}
