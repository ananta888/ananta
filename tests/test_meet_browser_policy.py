"""Hub-only explicit public origins and independent navigation/presentation rights."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_browser_policy import MeetBrowserPolicy
from agent.services.meet_contract import MeetError
from tests.test_meet_browser_workspace_contract import job


def row():
    value = job()
    return {
        "tenant_id": value["tenant_id"],
        "project_id": value["project_id"],
        "owner_subject": "owner",
        "policy_id": value["policy_id"],
        "revision": 1,
        "allowed_origins": ["https://example.com"],
        "operations": ["navigate", "present"],
    }


def scope():
    value = job()
    return SimpleNamespace(
        **{key: value[key] for key in ("tenant_id", "project_id", "runtime_id", "session_id")},
        owner_subject="owner",
        task_id=value["parent_task_id"],
        lease_id=value["parent_lease_id"],
        browser_workspace=True,
        capabilities=("screen.publish",),
    )


def test_operator_row_cannot_be_mutated_through_caller_containers():
    value = row()
    policies = MeetBrowserPolicy([value])
    value["allowed_origins"].clear()
    value["operations"].clear()
    policy, request = policies.navigation(scope(), "https://example.com/docs")
    assert policy.revision == 1 and request == job()["fetch"]
    assert policies.current_job(scope(), job(), present=True) == policy
    with pytest.raises(TypeError):
        policies.policies[("tenant", "project", "owner")] = policy


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "owner_subject", "browser_workspace", "capabilities"])
def test_wrong_scope_or_room_membership_alone_grants_nothing(field):
    value = scope()
    setattr(value, field, {"browser_workspace": False, "capabilities": ()}.get(field, "other"))
    with pytest.raises(MeetError, match="policy_denied"):
        MeetBrowserPolicy([row()]).navigation(value, "https://example.com/docs")


@pytest.mark.parametrize("operations", [["navigate"], ["present"]])
def test_navigation_and_presentation_are_distinct_permissions(operations):
    policies = MeetBrowserPolicy([row() | {"operations": operations}])
    if operations == ["navigate"]:
        policies.current_job(scope(), job())
        with pytest.raises(MeetError, match="policy_denied"):
            policies.current_job(scope(), job(), present=True)
    else:
        policies.require(scope(), "present")
        with pytest.raises(MeetError, match="policy_denied"):
            policies.navigation(scope(), "https://example.com/docs")


@pytest.mark.parametrize(
    "url",
    [
        "https://child.example.com/docs",
        "http://example.com",
        "https://example.com:444",
        "https://example.com/login",
        "https://127.0.0.1",
        "https://metadata.google.internal",
    ],
)
def test_exact_origin_and_existing_browser_admission_remain_required(url):
    with pytest.raises(MeetError, match="navigation_denied"):
        MeetBrowserPolicy([row()]).navigation(scope(), url)


def test_existing_browser_task_policy_denial_is_not_overridden_by_origin_approval():
    browser = Mock()
    browser.enforce_domain.return_value.allow = False
    with pytest.raises(MeetError, match="navigation_denied"):
        MeetBrowserPolicy([row()], browser_policy=browser).navigation(scope(), "https://example.com/docs")
    contract = browser.enforce_domain.call_args.kwargs["contract"]
    assert contract.max_actions == 1 and contract.timeout_seconds == 30
    assert contract.auth_policy == "none" and contract.download_policy == "deny" and not contract.persist_session


@pytest.mark.parametrize(
    "changes",
    [
        {"policy_id": "other"},
        {"policy_revision": 2},
        {"parent_task_id": "other"},
        {"parent_lease_id": "other"},
        {"runtime_id": "other"},
        {"session_id": "other"},
        {"tenant_id": "other"},
    ],
)
def test_old_policy_or_borrowed_task_bindings_are_not_current(changes):
    with pytest.raises(MeetError, match="policy_denied"):
        MeetBrowserPolicy([row()]).current_job(scope(), job() | changes)


def test_expansion_or_removal_of_operator_origins_invalidates_old_exact_projection():
    with pytest.raises(MeetError, match="policy_denied"):
        MeetBrowserPolicy([row() | {"allowed_origins": ["https://example.com", "https://second.example"]}]).current_job(
            scope(), job()
        )
    with pytest.raises(MeetError, match="policy_denied"):
        MeetBrowserPolicy([]).current_job(scope(), job())


@pytest.mark.parametrize(
    "changes",
    [
        {"operations": []},
        {"operations": ["navigate"] * 2},
        {"operations": ["click"]},
        {"operations": [None]},
        {"allowed_origins": ["https://example.com/"]},
        {"allowed_origins": []},
        {"revision": True},
        {"policy_id": "RUN_policy"},
        {"extra": True},
    ],
)
def test_closed_operator_configuration_rejects_ambiguous_or_overbroad_rows(changes):
    with pytest.raises(ValueError):
        MeetBrowserPolicy([row() | changes])


def test_duplicate_policy_scope_denied():
    value = row()
    with pytest.raises(ValueError):
        MeetBrowserPolicy([value, deepcopy(value)])
