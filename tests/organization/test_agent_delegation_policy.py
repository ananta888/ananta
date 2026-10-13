"""PAO-004: hub-only hierarchical delegation policy, fail-closed."""

from __future__ import annotations

import pytest

from agent.services.agent_delegation_policy import (
    DelegationProposal,
    DelegationScope,
    HubDelegationPolicy,
)

REPORTING = {"dev": "lead", "lead": "ceo", "ceo": None}
CAPABILITIES = {"ceo": {"plan", "code", "review"}, "lead": {"code", "review"}, "dev": {"code"}}


def proposal(**changes) -> DelegationProposal:
    base = dict(proposal_id="p1", proposer_role_key="lead", target_role_key="dev", goal_id="goal-1",
                project_id="proj-1", task_id="task-1", capability="code", requested_depth=1)
    base.update(changes)
    return DelegationProposal(**base)


def scope(**changes) -> DelegationScope:
    base = dict(goal_id="goal-1", project_id="proj-1", max_depth=3)
    base.update(changes)
    return DelegationScope(**base)


def policy(**changes) -> HubDelegationPolicy:
    return HubDelegationPolicy(reporting=changes.get("reporting", REPORTING))


def test_manager_can_delegate_a_granted_capability_down_three_levels():
    result = policy().validate(proposal(), role_capabilities=CAPABILITIES, scope=scope())
    assert result.allowed is True and result.reason == "delegation_admitted"
    assert result.requires_hub_task_mutation is True
    # ceo -> lead -> dev: the ceo may delegate to either level.
    assert policy().validate(proposal(proposer_role_key="ceo", target_role_key="dev"),
                             role_capabilities=CAPABILITIES, scope=scope()).allowed


def test_subordinate_cannot_delegate_upward():
    result = policy().validate(proposal(proposer_role_key="dev", target_role_key="lead"),
                               role_capabilities=CAPABILITIES, scope=scope())
    assert result.allowed is False and result.reason == "delegation_not_in_command_chain"


def test_capability_must_be_granted_to_the_target():
    result = policy().validate(proposal(capability="plan"), role_capabilities=CAPABILITIES, scope=scope())
    assert result.allowed is False and result.reason == "delegation_capability_not_granted"


def test_proposer_cannot_delegate_a_capability_it_does_not_hold():
    caps = {"ceo": {"plan", "code", "review"}, "lead": {"code"}, "dev": {"review", "code"}}
    result = policy().validate(proposal(capability="review"), role_capabilities=caps, scope=scope())
    assert result.allowed is False and result.reason == "delegation_capability_escalation"


def test_scope_and_depth_are_bounded():
    assert policy().validate(proposal(), role_capabilities=CAPABILITIES,
                             scope=scope(goal_id="other")).reason == "delegation_scope_violation"
    assert policy().validate(proposal(requested_depth=4), role_capabilities=CAPABILITIES,
                             scope=scope(max_depth=2)).reason == "delegation_depth_exceeded"


def test_self_reference_and_reporting_cycle_fail_closed():
    assert policy().validate(proposal(target_role_key="lead"), role_capabilities=CAPABILITIES,
                             scope=scope()).reason == "delegation_self_reference"
    cyclic = {"dev": "lead", "lead": "dev", "ceo": None}
    assert policy(reporting=cyclic).validate(proposal(), role_capabilities=CAPABILITIES,
                                             scope=scope()).reason == "delegation_reporting_cycle"


def test_unknown_role_is_not_in_the_command_chain():
    assert policy().validate(proposal(target_role_key="ghost"), role_capabilities=CAPABILITIES,
                             scope=scope()).reason == "delegation_not_in_command_chain"


@pytest.mark.parametrize("bad", [
    {"proposal_id": ""}, {"proposer_role_key": ""}, {"capability": ""}, {"requested_depth": 0},
    {"requested_depth": 99},
])
def test_invalid_proposals_are_rejected(bad):
    result = policy().validate(proposal(**bad), role_capabilities=CAPABILITIES, scope=scope())
    assert result.allowed is False
