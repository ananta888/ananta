from __future__ import annotations

import pytest

from agent.services.agent_template_registry import AgentTemplateRegistry
from agent.services.workspace_context_policy import WorkspaceContextPolicy, WorkspaceContextPolicyResolver


class TestGoalConfigContextPolicyValidation:
    """Tests for CSD-010/012: workspace_context_policy in goal config."""

    def test_create_goal_with_valid_selective_policy(self):
        registry = AgentTemplateRegistry()
        resolver = WorkspaceContextPolicyResolver(template_registry=registry)
        goal_config = {"workspace_context_policy": {"scope_mode": "selective", "max_files": 30}}
        policy = resolver.resolve(goal_config, "coding", None)
        assert policy.scope_mode == "selective"
        assert policy.max_files == 30

    def test_effective_config_contains_defaults_when_not_set(self):
        registry = AgentTemplateRegistry()
        resolver = WorkspaceContextPolicyResolver(template_registry=registry)
        policy = resolver.resolve({}, "coding", None)
        assert isinstance(policy, WorkspaceContextPolicy)

    def test_scope_mode_selective_is_preserved(self):
        registry = AgentTemplateRegistry()
        resolver = WorkspaceContextPolicyResolver(template_registry=registry)
        goal_config = {"workspace_context_policy": {"scope_mode": "selective"}}
        policy = resolver.resolve(goal_config, "analysis", None)
        assert policy.scope_mode == "selective"

    def test_scope_mode_none_is_preserved(self):
        registry = AgentTemplateRegistry()
        resolver = WorkspaceContextPolicyResolver(template_registry=registry)
        goal_config = {"workspace_context_policy": {"scope_mode": "none"}}
        policy = resolver.resolve(goal_config, "coding", None)
        assert policy.scope_mode == "none"


class TestAdminApiContextPolicyValidation:
    """Tests for CSD-011: Admin API validation via _validate_context_policy."""

    def test_valid_scope_mode_selective(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"scope_mode": "selective"})
        assert errors == []

    def test_invalid_scope_mode(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"scope_mode": "invalid"})
        assert any("scope_mode" in e for e in errors)

    def test_invalid_sensitivity_ceiling(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"sensitivity_ceiling": "topsecret"})
        assert any("sensitivity_ceiling" in e for e in errors)

    def test_valid_sensitivity_ceiling(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"sensitivity_ceiling": "confidential"})
        assert errors == []

    def test_invalid_max_files_zero(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"max_files": 0})
        assert any("max_files" in e for e in errors)

    def test_invalid_max_files_string(self):
        from agent.routes.admin.agent_templates import _validate_context_policy
        errors = _validate_context_policy({"max_files": "not_a_number"})
        assert any("max_files" in e for e in errors)


class TestAgentTemplateRegistry:
    def test_get_defaults_for_known_template(self):
        registry = AgentTemplateRegistry()
        defaults = registry.get_context_policy_defaults("code-reviewer")
        assert defaults is not None
        assert defaults.get("scope_mode") == "selective"

    def test_get_defaults_for_unknown_template(self):
        registry = AgentTemplateRegistry()
        assert registry.get_context_policy_defaults("nonexistent") is None

    def test_get_defaults_for_none(self):
        registry = AgentTemplateRegistry()
        assert registry.get_context_policy_defaults(None) is None

    def test_register_override_takes_priority(self):
        registry = AgentTemplateRegistry()
        registry.register_override("code-reviewer", {"scope_mode": "full"})
        defaults = registry.get_context_policy_defaults("code-reviewer")
        assert defaults["scope_mode"] == "full"

    def test_clear_override_restores_default(self):
        registry = AgentTemplateRegistry()
        registry.register_override("code-reviewer", {"scope_mode": "full"})
        registry.clear_override("code-reviewer")
        defaults = registry.get_context_policy_defaults("code-reviewer")
        assert defaults["scope_mode"] == "selective"

    def test_list_templates_returns_all_known(self):
        registry = AgentTemplateRegistry()
        templates = registry.list_templates()
        ids = [t["id"] for t in templates]
        assert "code-reviewer" in ids
        assert "bugfix-specialist" in ids
        assert "architecture-analyst" in ids
