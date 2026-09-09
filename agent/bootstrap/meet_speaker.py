"""Explicit Hub-only activation; legacy deployments do not infer a new policy."""

import os


def configured_speaker_floor(engine):
    enabled = os.environ.get("ANANTA_MEET_SPEAKER_FLOOR", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("meet_speaker_config_invalid")
    if enabled == "0":
        return None
    from agent.repositories.meet_speaker_floor import SqlMeetSpeakerFloor
    from agent.services.meet_dialog_speaker_floor import MeetDialogSpeakerFloor
    from agent.services.meet_speaker_floor import MeetSpeakerFloor

    states = SqlMeetSpeakerFloor(engine)
    states.initialize()
    return MeetDialogSpeakerFloor(MeetSpeakerFloor(states), states)
