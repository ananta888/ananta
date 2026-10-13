"""Closed model-child configuration for the installed assigned Companion profile."""

import os
import time

from ananta_contracts.meet_dialog import validate_assignment


def assigned_model_environment(assignment, *, environment=None):
    """Keep provider/TTS settings, without legacy Hub tool/retrieval authority.

    This filters supported child ports; it is not filesystem/network isolation.
    Assignment-bound Hub tool delegation needs separate explicit negotiation.
    """
    value = validate_assignment(assignment, time.time())
    if value.get("companion_execution") is not True:
        raise ValueError("meet_companion_assignment_profile_invalid")
    child = dict(os.environ if environment is None else environment)
    for name in tuple(child):
        if name.startswith("MEET_HUB_") or name == "MEET_WORKER_KEY_FILE":
            child.pop(name)
    child.update({"ANANTA_MEET_AVATAR_CODECOMPASS": "0", "MEET_COMPANION_TOOLS": "0",
                  "MEET_COMPANION_RAG_PREFIX": "0", "MEET_COMPANION_PROJECT": value["project_id"],
                  "MEET_LLM_TOOL_RESULT_CHARS": "6000"})
    return child
