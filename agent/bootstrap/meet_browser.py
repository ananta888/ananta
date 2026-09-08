"""Default-deny operator composition for the additive public browser workspace."""

import os

from agent.services.meet_browser_policy import MeetBrowserPolicy
from agent.services.meet_browser_tasks import HubBrowserTasks
from agent.services.meet_browser_workspaces import MeetBrowserWorkspaces
from ananta_contracts.persona_inspection_wire import parse_inspection_json


def configured_browser_workspaces(authority, tasks):
    try:
        raw = os.environ.get("ANANTA_MEET_BROWSER_PUBLIC_POLICIES", "[]").encode("utf-8")
        rows = parse_inspection_json(raw, maximum=131072)
        policy = MeetBrowserPolicy(rows)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("meet_browser_operator_policy_invalid") from None
    return MeetBrowserWorkspaces(authority, HubBrowserTasks(tasks), policy)
