"""Explicit Hub-only reconnect policy; never infer it from network failures."""

import os


def configured_dialog_recovery(engine, authority, meet, issuer, phases, *, speaker_floor=None):
    enabled = os.environ.get("ANANTA_MEET_DIALOG_RECONNECT", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("meet_reconnect_config_invalid")
    if enabled == "0":
        return None
    if any(port is None for port in (authority, meet, issuer, phases)):
        raise ValueError("meet_reconnect_coordinators_required")
    from agent.repositories.meet_dialog_recovery import SqlDialogRecovery
    from agent.services.meet_dialog_recovery import MeetDialogRecovery

    states = SqlDialogRecovery(engine)
    states.initialize()
    return MeetDialogRecovery(authority, states, meet, issuer, phases, speaker_floor=speaker_floor)
