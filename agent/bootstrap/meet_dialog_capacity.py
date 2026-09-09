"""Strict operator capacity configuration; never accepted from a dialog caller."""

import json
import os

from agent.models.meet_dialog_capacity import DialogCapacityPolicy
from agent.repositories.meet_dialog_capacity import SqlDialogCapacity
from agent.services.meet_dialog_capacity import MeetDialogCapacity


def configured_dialog_capacity(app, engine, authority):
    try:
        value = json.loads(os.environ.get("ANANTA_MEET_DIALOG_CAPACITY", "{}"))
        if not isinstance(value, dict) or set(value) - {"sessions", "publisher_sessions", "publication_bps"}:
            raise ValueError()
        policy = DialogCapacityPolicy(**value)
    except (TypeError, ValueError):
        raise ValueError("meet_dialog_capacity_config_invalid") from None
    slots = SqlDialogCapacity(engine, os.environ.get("ANANTA_MEET_DIALOG_CAPACITY_POOL", "local-meet-dialog"), policy)
    slots.initialize()
    service = MeetDialogCapacity(slots, authority)
    app.extensions["meet_dialog_capacity"] = service
    return service
