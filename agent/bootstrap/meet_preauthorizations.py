"""Default-off additional Hub operator fence; never automatically grants policy."""

import os


def configure_meet_preauthorizations(app, engine):
    if os.environ.get("ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED") != "1":
        return None
    if app.config.get("ROLE") != "hub":
        raise ValueError("meet_preauthorization_hub_required")
    from agent.repositories.meet_preauthorizations import SqlMeetPreauthorizations
    from agent.services.meet_dialog_preauthorization import MeetDialogPreauthorization

    store = SqlMeetPreauthorizations(engine)
    store.initialize()
    authority = MeetDialogPreauthorization(store)
    app.extensions["meet_dialog_preauthorization"] = authority
    return authority
