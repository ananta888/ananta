"""Deterministic Hub principal bindings; never synthetic release-evidence IDs."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from agent.models.meet_machine_principal import (
    current_machine_principal,
    principal_from_role_facts,
    principal_from_verified_role,
)
from agent.models.meet_organization_topology import MeetTopologyScope
from agent.models.meet_role_assignment import binding_projection
from tests.test_meet_role_assignment_policy import facts


def role_binding():
    return binding_projection(
        "task",
        "parent",
        "lease",
        "runtime",
        MeetTopologyScope("tenant", "project", "organization", "unit", "team", "role"),
        facts(),
    )


def test_preflight_derivation_uses_eligible_typed_facts_without_execution_identifiers():
    binding = role_binding()
    scope = MeetTopologyScope(**binding["scope"])
    assert principal_from_role_facts(scope, facts()) == principal_from_verified_role(binding)
    for malformed_scope, malformed_facts in [(scope, {}), ({}, facts()), (scope, replace(facts(), status="offline"))]:
        with pytest.raises(ValueError):
            principal_from_role_facts(malformed_scope, malformed_facts)


def test_principal_is_immutable_stable_across_tasks_and_contains_no_publisher_url():
    binding = role_binding()
    principal = principal_from_verified_role(binding)
    assert principal.subject.startswith("org-agent-") and len(principal.subject) == 74
    assert principal_from_verified_role(binding | {"task_id": "another", "lease_id": "next"}) == principal
    assert binding["publisher_url"] not in str(principal.projection())
    assert principal.assignment_id == facts().assignment_id
    with pytest.raises(FrozenInstanceError):
        principal.organization_id = "foreign"
    stored = {"meet_machine_principal": principal.projection()}
    assert current_machine_principal(stored, binding) == principal
    stored["meet_machine_principal"]["assignment_id"] = "foreign"
    with pytest.raises(ValueError, match="changed"):
        current_machine_principal(stored, binding)


@pytest.mark.parametrize(
    "field", ["tenant_id", "project_id", "organization_id", "role_slot_id", "assignment_id", "agent_ref"]
)
def test_every_authoritative_identity_dimension_separates_the_subject(field):
    original = principal_from_verified_role(role_binding())
    changed = replace(original, **{field: "a" * 64 if field == "agent_ref" else "different"})
    assert changed.subject != original.subject


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "untrusted"),
        ("task_id", None),
        ("lease_id", "../escape"),
        ("runtime_id", True),
        ("parent_task_id", None),
        ("snapshot_digest", "x"),
        ("assignment_id", "*"),
        ("publisher_url", "http://user:secret@publisher:80"),
        ("publisher_url", "http://publisher:8091/path"),
        ("extra", "invented"),
        ("scope", {}),
    ],
)
def test_malformed_verified_binding_projection_is_not_coerced(key, value):
    with pytest.raises(ValueError, match="^meet_machine_principal_invalid$"):
        principal_from_verified_role(role_binding() | {key: value})


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "organization_id", "role_slot_id", "unit_id", "team_id"])
def test_foreign_or_malformed_scope_projection_fails_closed(field):
    binding = role_binding()
    with pytest.raises(ValueError, match="invalid"):
        principal_from_verified_role(binding | {"scope": binding["scope"] | {field: ["foreign"]}})


@pytest.mark.parametrize("value", [None, {}, [], "ananta", {"subject": "ananta"}])
def test_present_bad_principal_is_never_legacy(value):
    with pytest.raises(ValueError):
        current_machine_principal({"meet_machine_principal": value}, role_binding())
    assert current_machine_principal({}, None) is None


def test_missing_or_changed_verified_assignment_cannot_reuse_an_existing_principal():
    binding = role_binding()
    stored = {"meet_machine_principal": principal_from_verified_role(binding).projection()}
    for changed in [None, {}, binding | {"assignment_id": "other"}, binding | {"publisher_url": "http://other:8091"}]:
        with pytest.raises(ValueError):
            current_machine_principal(deepcopy(stored), changed)
