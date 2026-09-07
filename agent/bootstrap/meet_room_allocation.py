"""Explicit operator configuration for headless room assignment, independent of media."""

import json
import os
import re


def allocation_scopes(raw):
    try:
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError()
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 128:
            raise ValueError()
        scopes = set()
        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != 2
                or any(not isinstance(v, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", v) is None for v in row)
                or tuple(row) in scopes
            ):
                raise ValueError()
            scopes.add(tuple(row))
        return frozenset(scopes)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("meet_room_allocation_policy_invalid") from None


def configure_meet_room_allocation(app):
    flag = "ANANTA_MEET_ROOM_ALLOCATION_ENABLED"
    if str(app.config.get(flag, os.environ.get(flag, "0"))).lower() not in {"1", "true"}:
        return
    if app.config.get("ROLE") != "hub" or "meet_binding_service" not in app.extensions:
        raise ValueError("meet_room_allocation_hub_required")
    from agent.services.meet_room_allocation import MeetRoomAllocation

    key = "ANANTA_MEET_ROOM_ALLOCATION_SCOPES"
    scopes = allocation_scopes(app.config.get(key, os.environ.get(key, "[]")))
    binding = app.extensions["meet_binding_service"]
    app.extensions["meet_room_allocation"] = MeetRoomAllocation(binding, scopes, invite=binding.profile.invite)
