"""Negotiated bounded reconnect handoff, not a new or extended assignment."""

from copy import deepcopy

from ananta_contracts.meet_dialog import validate_meeting

MAX_RECOVERIES = 2
RECOVERY_MS = 30_000
RECOVERY_QUIET_MS = 4_000


def negotiated_reconnect_fields(enabled):
    return {"reconnect": True} if enabled is True else {}


def validate_reconnect_response(value, request, assignment, now_ms, previous_attempt):
    if (
        assignment.get("reconnect") is not True
        or request.get("action") != "reconnect"
        or type(previous_attempt) is not int
        or not 0 <= previous_attempt <= MAX_RECOVERIES
        or type(request.get("attempt")) is not int
        or not 0 <= request["attempt"] <= MAX_RECOVERIES
        or not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "attempt", "state", "deadline_ms", "ready_ms", "meeting"}
        or value["schema"] != "ananta.meet-reconnect-state.v1"
        or value["nonce"] != request.get("nonce")
        or type(value["attempt"]) is not int
        or not 1 <= value["attempt"] <= MAX_RECOVERIES
        or value["attempt"] != (previous_attempt + 1 if request["attempt"] == 0 else previous_attempt)
        or request["attempt"] != 0
        and request["attempt"] != value["attempt"]
        or type(value["state"]) is not str
        or value["state"] not in {"waiting", "joining"}
        or type(value["deadline_ms"]) is not int
        or not now_ms < value["deadline_ms"] <= min(assignment["deadline"] * 1000, now_ms + RECOVERY_MS)
        or type(value["ready_ms"]) is not int
        or not 0 < value["ready_ms"] < value["deadline_ms"]
        or value["state"] == "waiting"
        and value["meeting"] is not None
        or value["state"] == "joining"
        and now_ms < value["ready_ms"]
    ):
        raise ValueError("meet_reconnect_response_invalid")
    if value["meeting"] is not None:
        meeting = validate_meeting(value["meeting"])
        if any(meeting[key] != assignment["meeting"][key] for key in ("origin", "room_id")):
            raise ValueError("meet_reconnect_meeting_changed")
    return deepcopy(value)
