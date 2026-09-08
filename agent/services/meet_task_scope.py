"""Strict organization tuple projection shared by Hub-owned Meet task adapters."""

import re

from agent.services.meet_contract import MeetError
from agent.services.task_organization_scope import ORGANIZATION_SCOPE_FIELDS


def organization_tuple(task) -> dict[str, str]:
    """Copy whole scope without coercion or silently dropping malformed fields."""
    scope = {}
    for field in ORGANIZATION_SCOPE_FIELDS:
        value = getattr(task, field, None)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,191}", value):
            raise MeetError("meet_dialog_organization_scope_invalid", 403)
        scope[field] = value
    # A legacy team may exist outside an organization; units/slots may not.
    if not scope.get("organization_id") and (scope.get("unit_id") or scope.get("role_slot_id")):
        raise MeetError("meet_dialog_organization_scope_invalid", 403)
    return scope
