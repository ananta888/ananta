"""Hub-owned gradual retirement of unassigned Companion entrypoints."""

import os

from agent.services.meet_contract import MeetError

LEGACY_PATHS = frozenset({"/internal/companion", "/internal/assist/retrieve", "/internal/assist/tool"})


def require_legacy_companion(app, path):
    if app.config.get("ROLE") != "hub" or not any(path.endswith(value) for value in LEGACY_PATHS):
        return
    name = "ANANTA_MEET_COMPANION_LEGACY_ENABLED"
    value = app.config.get(name, os.environ.get(name))
    if value is None:
        enabled = "meet_companion_task_grants" not in app.extensions
    elif type(value) is bool:
        enabled = value
    elif isinstance(value, str) and value.lower() in {"1", "true", "0", "false"}:
        enabled = value.lower() in {"1", "true"}
    else:
        raise MeetError("meet_companion_legacy_policy_invalid", 503)
    if not enabled:
        raise MeetError("meet_companion_legacy_retired_use_hub_tasks", 410)
