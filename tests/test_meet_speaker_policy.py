"""Only exact verified Hub roles can receive explicit speech interruption policy."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from agent.models.meet_machine_principal import MeetMachinePrincipal
from agent.services.meet_speaker_policy import MeetSpeakerPolicy


def rule():
    return {
        "tenant_id": "tenant",
        "project_id": "project",
        "organization_id": "org",
        "role_slot_id": "chair",
        "policy_id": "chair-floor",
        "revision": 1,
        "priority": 2,
        "barge_in": True,
    }


def scope():
    return SimpleNamespace(
        tenant_id="tenant",
        project_id="project",
        machine_principal=MeetMachinePrincipal(
            "tenant",
            "project",
            "org",
            "chair",
            "assignment",
            "a" * 64,
        ),
    )


def test_exact_role_policy_is_immutable_and_records_a_non_evidence_digest():
    row = rule()
    policy = MeetSpeakerPolicy([row])
    row["priority"] = 0
    decision = policy.decide(scope())
    assert decision.priority == 2 and decision.barge_in is True and decision.organization_id == "org"
    assert len(decision.policy_digest) == 64
    with pytest.raises(FrozenInstanceError):
        decision.priority = 0
    with pytest.raises(TypeError):
        policy.rules[("foreign",)] = decision


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "organization_id", "role_slot_id"])
def test_different_verified_scope_cannot_inherit_high_priority_or_barge_in(field):
    value = scope()
    value.machine_principal = replace(value.machine_principal, **{field: "other"})
    if field in {"tenant_id", "project_id"}:
        setattr(value, field, "other")
    decision = MeetSpeakerPolicy([rule()]).decide(value)
    assert decision.priority == 0 and decision.barge_in is False


def test_unassigned_legacy_principal_is_fifo_only_and_forged_principal_is_rejected():
    value = scope()
    value.machine_principal = None
    assert MeetSpeakerPolicy([rule()]).decide(value).barge_in is False
    for forged in (rule(), SimpleNamespace(**rule()), "chair"):
        value.machine_principal = forged
        with pytest.raises(ValueError, match="scope_invalid"):
            MeetSpeakerPolicy([rule()]).decide(value)
    value = scope()
    value.tenant_id = "other"
    with pytest.raises(ValueError, match="scope_invalid"):
        MeetSpeakerPolicy([rule()]).decide(value)


@pytest.mark.parametrize(
    "patch",
    [
        {"priority": True},
        {"priority": -1},
        {"priority": 3},
        {"priority": 0},
        {"barge_in": 1},
        {"revision": True},
        {"revision": 0},
        {"revision": 2**31},
        {"role_slot_id": "*"},
        {"organization_id": ""},
        {"tenant_id": []},
        {"extra": True},
        {"policy_id": "SRC_fake"},
        {"policy_id": "RUN_fake"},
    ],
)
def test_malformed_or_broadened_policy_is_rejected(patch):
    with pytest.raises(ValueError, match="policy_invalid"):
        MeetSpeakerPolicy([rule() | patch])


@pytest.mark.parametrize("rows", [None, {}, True, [None], [rule()] * 65])
def test_policy_container_and_count_are_bounded(rows):
    with pytest.raises(ValueError, match="policy_invalid"):
        MeetSpeakerPolicy(rows)


def test_duplicate_scope_cannot_silently_replace_a_rule():
    with pytest.raises(ValueError, match="policy_duplicate"):
        MeetSpeakerPolicy([rule(), rule() | {"revision": 2}])
    first = MeetSpeakerPolicy([rule()]).decide(scope())
    next_revision = MeetSpeakerPolicy([rule() | {"revision": 2}]).decide(scope())
    assert first.policy_digest != next_revision.policy_digest
