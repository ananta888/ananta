"""AWTCL-018: policy gate and registry tests for the worker tool loop."""
from agent.services.ananta_tool_policy_service import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DECISION_POLICY_BLOCKED,
    get_ananta_tool_policy_service,
)
from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service


def _evaluate(tool_name, **kwargs):
    return get_ananta_tool_policy_service().evaluate(tool_name=tool_name, **kwargs)


def test_registry_rejects_unknown_tools_deterministically():
    registry = get_ananta_tool_registry_service()
    assert registry.get_tool("does.not_exist") is None
    decision = _evaluate("does.not_exist")
    assert decision.decision == DECISION_POLICY_BLOCKED
    assert decision.rule_id == "unknown_tool_rejected"


def test_registry_specs_have_schema_and_policy_requirements():
    for spec in get_ananta_tool_registry_service().list_tools():
        assert spec.name and spec.category and spec.risk_class
        assert isinstance(spec.argument_schema, dict)
        assert spec.result_schema == "ananta_tool_result.v1"
        assert "requires_approval" in spec.policy_requirements


def test_read_only_tool_allowed_without_approval():
    decision = _evaluate("codecompass.search", allowed_tools=["codecompass.search"])
    assert decision.decision == DECISION_ALLOW
    assert decision.risk_class == "read"


def test_tool_outside_allowed_scope_is_blocked():
    decision = _evaluate("repo.grep", allowed_tools=["codecompass.search"])
    assert decision.decision == DECISION_POLICY_BLOCKED
    assert decision.rule_id == "allowed_tools_scope"


def test_hermes_shell_execution_is_blocked():
    # Hermes may only review; every other hermes capability is denied and
    # unknown hermes tools are rejected by the registry.
    assert _evaluate("hermes.review").decision == DECISION_ALLOW
    assert _evaluate("hermes.shell_execute").decision == DECISION_POLICY_BLOCKED
    assert _evaluate("shell.run_unrestricted").decision == DECISION_POLICY_BLOCKED


def test_opencode_mutation_blocked_propose_allowed():
    assert _evaluate("opencode.propose").decision == DECISION_ALLOW
    blocked = _evaluate("external_worker.execute_mutation")
    assert blocked.decision == DECISION_POLICY_BLOCKED
    assert blocked.rule_id == "blocked_category"


def test_blocked_tools_never_run_via_worker_loop():
    for tool in ["shell.run_unrestricted", "network.fetch_arbitrary", "service.restart", "secret.read", "git.push", "git.commit"]:
        decision = _evaluate(tool)
        assert decision.decision == DECISION_POLICY_BLOCKED, tool
        assert decision.reason == "blocked_without_separate_approval"


def test_write_tool_blocked_in_read_only_mode():
    decision = _evaluate("repo.write_file", mutation_mode="read_only")
    assert decision.decision == DECISION_POLICY_BLOCKED
    assert decision.rule_id == "mutation_mode_gate"


def test_write_tool_allowed_in_controlled_workspace():
    decision = _evaluate("repo.write_file", mutation_mode="controlled_workspace")
    assert decision.decision == DECISION_ALLOW


def test_apply_patch_only_in_strict_patch_request():
    blocked = _evaluate("repo.apply_patch", mutation_mode="controlled_workspace")
    assert blocked.decision == DECISION_POLICY_BLOCKED
    allowed = _evaluate("repo.apply_patch", mutation_mode="strict_patch_request")
    assert allowed.decision == DECISION_ALLOW


def test_approval_required_write_tool_without_grant():
    decision = _evaluate("git.add_selected", mutation_mode="controlled_workspace")
    assert decision.decision == DECISION_APPROVAL_REQUIRED
    granted = _evaluate(
        "git.add_selected", mutation_mode="controlled_workspace", approvals=["git.add_selected"]
    )
    assert granted.decision == DECISION_ALLOW


def test_shell_allowlisted_requires_approval():
    decision = _evaluate("shell.run_allowlisted")
    assert decision.decision == DECISION_APPROVAL_REQUIRED
