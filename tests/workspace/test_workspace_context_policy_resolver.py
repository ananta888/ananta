from __future__ import annotations

import pytest

from agent.services.agent_template_registry import AgentTemplateRegistry
from agent.services.workspace_context_policy import (
    WorkspaceContextPolicy,
    WorkspaceContextPolicyResolver,
)


@pytest.fixture
def registry():
    return AgentTemplateRegistry()


@pytest.fixture
def resolver(registry):
    return WorkspaceContextPolicyResolver(template_registry=registry)


class TestWorkspaceContextPolicyResolver:
    def test_task_kind_coding_gives_selective_scope(self, resolver):
        policy = resolver.resolve({}, "coding", None)
        assert policy.scope_mode == "selective"
        assert policy.codecompass_profile == "subtask_refactor_navigation"

    def test_task_kind_analysis_gives_architecture_profile(self, resolver):
        policy = resolver.resolve({}, "analysis", None)
        assert policy.codecompass_profile == "subtask_architecture_review"

    def test_task_kind_bugfix_gives_bugfix_profile(self, resolver):
        policy = resolver.resolve({}, "bugfix", None)
        assert policy.codecompass_profile == "subtask_bugfix_local"

    def test_task_kind_ops_gives_config_profile(self, resolver):
        policy = resolver.resolve({}, "ops", None)
        assert policy.codecompass_profile == "subtask_config_integration"

    def test_goal_config_full_overrides_task_kind(self, resolver):
        goal_config = {"workspace_context_policy": {"scope_mode": "full"}}
        policy = resolver.resolve(goal_config, "coding", None)
        assert policy.scope_mode == "full"

    def test_unknown_task_kind_falls_back_to_full(self, resolver):
        policy = resolver.resolve({}, "git_commit", None)
        assert policy.scope_mode == "full"

    def test_unknown_task_kind_string(self, resolver):
        policy = resolver.resolve({}, "some_random_kind", None)
        assert policy.scope_mode == "full"

    def test_agent_template_overrides_task_kind_default(self, resolver):
        policy = resolver.resolve({}, "coding", "architecture-analyst")
        assert policy.codecompass_profile == "subtask_architecture_review"

    def test_goal_config_overrides_agent_template(self, resolver):
        goal_config = {"workspace_context_policy": {"codecompass_profile": "subtask_bugfix_local"}}
        policy = resolver.resolve(goal_config, "coding", "code-reviewer")
        assert policy.codecompass_profile == "subtask_bugfix_local"

    def test_resolver_is_pure_no_side_effects(self, resolver):
        p1 = resolver.resolve({}, "coding", None)
        p2 = resolver.resolve({}, "coding", None)
        assert p1.scope_mode == p2.scope_mode
        assert p1.codecompass_profile == p2.codecompass_profile

    def test_result_is_workspace_context_policy_instance(self, resolver):
        policy = resolver.resolve({}, "coding", None)
        assert isinstance(policy, WorkspaceContextPolicy)

    def test_max_files_from_goal_config(self, resolver):
        goal_config = {"workspace_context_policy": {"max_files": 42}}
        policy = resolver.resolve(goal_config, "coding", None)
        assert policy.max_files == 42

    def test_none_task_kind_returns_system_default(self, resolver):
        policy = resolver.resolve({}, None, None)
        assert policy.scope_mode == "full"
