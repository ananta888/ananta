"""Immutable browser job pointer with bounded Hub CAS selection revisions."""

from ananta_contracts.meet_browser_workspace import integer, validate_browser_job


def initial_browser_state():
    return {"schema": "ananta.meet-browser-workspace-state.v1", "revision": 1, "mode": "status", "job": None}


def browser_state(value):
    if type(value) is not dict or set(value) != {"schema", "revision", "mode", "job"}:
        raise ValueError("meet_browser_state_invalid")
    if value["schema"] != "ananta.meet-browser-workspace-state.v1" or value["mode"] not in ("status", "browser", "off"):
        raise ValueError("meet_browser_state_invalid")
    integer(value["revision"], 1023)
    job = None if value["job"] is None else validate_browser_job(value["job"])
    if (
        job is None
        and value["mode"] == "browser"
        or job is not None
        and (job["navigation_revision"] > value["revision"] or value["mode"] == "status")
    ):
        raise ValueError("meet_browser_state_invalid")
    return dict(value) | {"job": job}
