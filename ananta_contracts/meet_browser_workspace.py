"""Closed Hub browser job and publication projection; neither is a self-issued grant."""

import re
from copy import deepcopy

from ananta_contracts.browser_public_fetch import validate_fetch_request
from ananta_contracts.browser_view_generation import BrowserViewGeneration

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
JOB_IDS = (
    "task_id",
    "lease_id",
    "workspace_id",
    "page_id",
    "parent_task_id",
    "parent_lease_id",
    "runtime_id",
    "session_id",
    "tenant_id",
    "project_id",
    "policy_id",
)
_BINDING_IDS = ("meet_session_id", "own_peer_id")


def integer(value, maximum=2**53 - 1):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("meet_browser_integer_invalid")
    return value


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("meet_browser_identity_invalid")
    return value


def validate_browser_job(value):
    fields = {*JOB_IDS, "schema", "navigation_revision", "policy_revision", "issued_at_ms", "deadline_ms", "fetch"}
    if type(value) is not dict or set(value) != fields or value["schema"] != "ananta.meet-browser-job.v1":
        raise ValueError("meet_browser_job_invalid")
    for name in JOB_IDS:
        identifier(value[name])
    if value["policy_id"].startswith(("SRC_", "RUN_")):
        raise ValueError("meet_browser_policy_identity_invalid")
    integer(value["navigation_revision"], 1023)
    integer(value["policy_revision"], 2**31 - 1)
    issued, deadline = integer(value["issued_at_ms"]), integer(value["deadline_ms"])
    if not 0 < deadline - issued <= 30000 or value["task_id"] == value["parent_task_id"]:
        raise ValueError("meet_browser_job_invalid")
    request = validate_fetch_request(value["fetch"])
    return deepcopy(value) | {"fetch": request}


def browser_generation(job):
    job = validate_browser_job(job)
    return BrowserViewGeneration(job["workspace_id"], job["page_id"], job["navigation_revision"])


def validate_browser_source(value):
    fields = {"schema", "revision", "mode", "job", "binding", "reason"}
    if type(value) is not dict or set(value) != fields or value["schema"] != "ananta.meet-browser-source.v1":
        raise ValueError("meet_browser_source_invalid")
    integer(value["revision"], 1023)
    if type(value["mode"]) is not str or value["mode"] not in {"status", "browser", "off"}:
        raise ValueError("meet_browser_source_invalid")
    if value["job"] is None:
        if (
            value["binding"] is not None
            or value["mode"] == "browser"
            or value["reason"] not in ("not_selected", "task_inactive", "policy_denied", "expired")
        ):
            raise ValueError("meet_browser_source_invalid")
        return dict(value)
    job = validate_browser_job(value["job"])
    if value["mode"] == "status" or value["reason"] != "ready":
        raise ValueError("meet_browser_source_invalid")
    binding = value["binding"]
    fields = {*_BINDING_IDS, "generation", "membership_epoch", "screen_revision", "deadline_ms"}
    if type(binding) is not dict or set(binding) != fields:
        raise ValueError("meet_browser_binding_invalid")
    for name in _BINDING_IDS:
        identifier(binding[name])
    for name in fields - set(_BINDING_IDS):
        integer(binding[name], 1023 if name == "screen_revision" else 2**53 - 1)
    if binding["deadline_ms"] > job["deadline_ms"] or binding["deadline_ms"] <= job["issued_at_ms"]:
        raise ValueError("meet_browser_binding_invalid")
    return dict(value) | {"job": job, "binding": dict(binding)}


def require_browser_assignment(job, assignment):
    job = validate_browser_job(job)
    for field, parent in (
        ("parent_task_id", "task_id"),
        ("parent_lease_id", "lease_id"),
        *((name, name) for name in ("tenant_id", "project_id", "runtime_id", "session_id")),
    ):
        if job[field] != assignment.get(parent):
            raise ValueError("meet_browser_parent_mismatch")
    if assignment.get("browser_workspace") is not True or "screen.publish" not in assignment["capabilities"]:
        raise ValueError("meet_browser_not_negotiated")
    if job["deadline_ms"] > assignment["deadline"] * 1000:
        raise ValueError("meet_browser_parent_mismatch")
    return job
