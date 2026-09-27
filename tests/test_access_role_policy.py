"""WCRB-005: bound access roles decide operations and worker tools; without roles nothing changes."""

import pytest

from agent.services.access_roles import BUILTIN_ROLES, effective_grants
from agent.services.ananta_tool_policy_service import AnantaToolPolicyService
from agent.services.operation_policy_service import OperationAuthContext, OperationPolicyService
from agent.services.operation_registry_service import (
    ananta_tool_operation_id,
    get_operation_registry_service,
)

pytestmark = pytest.mark.timeout(30)
ROLES = {role_id: spec["grants"] for role_id, spec in BUILTIN_ROLES.items()}
ROLES["no-grep"] = {"allow_groups": ["ananta.tool.read.v1"], "deny_operations": ["ananta.tool.repo.grep"]}
REGISTRY = get_operation_registry_service()
POLICY = OperationPolicyService(REGISTRY)
ENFORCED = {"enabled": True, "enforced_transports": ["mcp.tool", "mcp.resource", "ananta.tool"],
            "allowed_auth_sources": ["user_jwt"], "allow_groups": ["mcp.read.v1"],
            "require_admin_for_access_classes": ["write", "admin"], "require_approval_for_risks": ["critical"]}


def grants(*role_ids):
    return effective_grants(ROLES, role_ids)


def test_worker_tools_are_operations_in_three_groups():
    read = REGISTRY.group_members("ananta.tool.read.v1")
    assert ananta_tool_operation_id("codecompass.search") in read and ananta_tool_operation_id("repo.grep") in read
    assert ananta_tool_operation_id("repo.write_file") in REGISTRY.group_members("ananta.tool.write.v1")
    assert ananta_tool_operation_id("test.run") in REGISTRY.group_members("ananta.tool.execution.v1")
    descriptor = REGISTRY.get(ananta_tool_operation_id("repo.write_file"))
    assert descriptor.transport == "ananta.tool" and descriptor.access_class == "write" and descriptor.side_effecting


@pytest.mark.parametrize("role, tool, allowed", [
    ("viewer", "codecompass.search", True), ("viewer", "test.run", False), ("viewer", "repo.write_file", False),
    ("developer", "test.run", True), ("developer", "repo.apply_patch", False),
    ("maintainer", "repo.apply_patch", True), ("admin", "repo.write_file", True),
    ("no-grep", "codecompass.search", True), ("no-grep", "repo.grep", False),
])
def test_builtin_roles_decide_worker_tools(role, tool, allowed):
    decision = POLICY.decide(REGISTRY.get(ananta_tool_operation_id(tool)), ENFORCED,
                             OperationAuthContext(auth_source="user_jwt", grants=grants(role)))
    assert decision.allowed is allowed, decision


def test_without_bound_roles_the_global_policy_decides_as_before():
    search = REGISTRY.get("mcp.tool.codecompass.retrieve")
    legacy = POLICY.decide(search, ENFORCED, OperationAuthContext(auth_source="user_jwt"))
    empty = POLICY.decide(search, ENFORCED, OperationAuthContext(auth_source="user_jwt", grants=grants()))
    assert legacy == empty and legacy.allowed and legacy.matched_rule_id == "allow:group:mcp.read.v1"


def test_a_global_deny_beats_every_role_and_a_disabled_operation_stays_disabled():
    denied = {**ENFORCED, "deny_operations": [ananta_tool_operation_id("repo.grep")]}
    decision = POLICY.decide(REGISTRY.get(ananta_tool_operation_id("repo.grep")), denied,
                             OperationAuthContext(auth_source="user_jwt", grants=grants("admin")))
    assert not decision.allowed and decision.reason_code == "operation_explicitly_denied"


def test_the_worker_tool_policy_applies_the_requesters_roles():
    policy = AnantaToolPolicyService()
    viewer = grants("viewer")
    assert policy.evaluate(tool_name="repo.grep", allowed_tools=["repo.grep"], grants=viewer).decision == "allow"
    blocked = policy.evaluate(tool_name="test.run", allowed_tools=["test.run"], grants=viewer)
    assert blocked.decision == "policy_blocked" and blocked.reason == "role_denied"
    assert blocked.rule_id == "deny:role:no_grant"
    # no roles bound: the previous scope and mutation-mode rules alone decide
    assert policy.evaluate(tool_name="repo.grep", allowed_tools=["repo.grep"]).decision == "allow"
