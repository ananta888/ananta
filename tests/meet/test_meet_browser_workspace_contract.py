"""Closed browser Task projections and exact parent/session assignment fences."""

from copy import deepcopy

import pytest

from ananta_contracts.meet_browser_workspace import (
    JOB_IDS,
    browser_generation,
    require_browser_assignment,
    validate_browser_job,
    validate_browser_source,
)


def job():
    return {
        **{name: "synthetic-" + name for name in JOB_IDS},
        "schema": "ananta.meet-browser-job.v1",
        "navigation_revision": 1,
        "policy_revision": 1,
        "issued_at_ms": 10000,
        "deadline_ms": 40000,
        "fetch": {
            "schema": "ananta.browser-public-fetch.v1",
            "url": "https://example.com/docs",
            "allowed_origins": ["https://example.com"],
        },
    }


def projection():
    return {
        "schema": "ananta.meet-browser-source.v1",
        "revision": 2,
        "mode": "browser",
        "reason": "ready",
        "job": job(),
        "binding": {
            "meet_session_id": "synthetic-meet",
            "own_peer_id": "synthetic-peer",
            "generation": 1,
            "membership_epoch": 2,
            "screen_revision": 1,
            "deadline_ms": 35000,
        },
    }


def parent():
    value = job()
    return {
        **{key: value[key] for key in ("tenant_id", "project_id", "session_id", "runtime_id")},
        "task_id": value["parent_task_id"],
        "lease_id": value["parent_lease_id"],
        "deadline": 100,
        "capabilities": ["screen.publish"],
        "browser_workspace": True,
    }


def test_valid_job_and_source_are_copied_without_expanding_identity():
    original = projection()
    copy = validate_browser_source(original)
    assert copy == original and copy is not original
    copy["job"]["fetch"]["allowed_origins"].clear()
    copy["binding"]["generation"] = 99
    assert original["job"] == job() and original["binding"]["generation"] == 1
    assert browser_generation(job()).navigation_revision == 1
    assert require_browser_assignment(job(), parent()) == job()


@pytest.mark.parametrize("field", JOB_IDS)
def test_job_identity_fields_cannot_be_absent_or_url_values(field):
    value = job()
    value[field] = "https://private.example"
    with pytest.raises(ValueError):
        validate_browser_job(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "other"},
        {"navigation_revision": True},
        {"navigation_revision": 1024},
        {"policy_revision": 0},
        {"issued_at_ms": 0},
        {"deadline_ms": 40001},
        {"deadline_ms": 10000},
        {"policy_id": "SRC_fake"},
        {"policy_id": "RUN_fake"},
        {"task_id": "synthetic-parent_task_id"},
        {"extra": True},
        {"fetch": {"url": "https://example.com"}},
    ],
)
def test_invalid_job_boundaries_denied(changes):
    with pytest.raises(ValueError):
        validate_browser_job(job() | changes)


@pytest.mark.parametrize(
    "field",
    [
        "task_id",
        "lease_id",
        "tenant_id",
        "project_id",
        "runtime_id",
        "session_id",
        "browser_workspace",
        "capabilities",
        "deadline",
    ],
)
def test_borrowed_or_unnegotiated_parent_never_executes(field):
    value = parent()
    value[field] = {"browser_workspace": False, "capabilities": [], "deadline": 20}.get(field, "other")
    with pytest.raises(ValueError):
        require_browser_assignment(job(), value)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "host-screen"},
        {"mode": "status"},
        {"reason": "private failure detail"},
        {"revision": True},
        {"binding": None},
        {"job": None},
        {"html": "not permitted"},
    ],
)
def test_invalid_source_shape_denied(changes):
    with pytest.raises(ValueError):
        validate_browser_source(projection() | changes)


@pytest.mark.parametrize(
    "changes",
    [{"deadline_ms": 40001}, {"deadline_ms": 10000}, {"screen_revision": 1024}, {"generation": True}, {"extra": 1}],
)
def test_invalid_publication_binding_denied(changes):
    value = projection()
    value["binding"].update(changes)
    with pytest.raises(ValueError):
        validate_browser_source(value)


@pytest.mark.parametrize(
    "mode,reason", [("status", "not_selected"), ("off", "task_inactive"), ("off", "policy_denied"), ("off", "expired")]
)
def test_idle_or_blocked_sources_carry_no_job_or_publication_lease(mode, reason):
    value = projection() | {"job": None, "binding": None, "mode": mode, "reason": reason}
    assert validate_browser_source(value) == value
    borrowed = deepcopy(value) | {"binding": projection()["binding"]}
    with pytest.raises(ValueError):
        validate_browser_source(borrowed)
