"""Tests for ProposePolicy and ProposePolicyService. FA-T002."""
from __future__ import annotations

import pytest

from agent.services.propose_policy import (
    ProposePolicy,
    STRATEGY_LEGACY_SGPT,
    STRATEGY_DETERMINISTIC_HANDLER,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_HUMAN_REVIEW,
    SAFE_DEFAULT_STRATEGY_ORDER,
    build_policy_from_dict,
)
from agent.services.propose_policy_service import ProposePolicyService


# ── ProposePolicy validation ───────────────────────────────────────────────────

class TestProposePolicy:
    def test_default_policy_valid(self):
        p = ProposePolicy()
        assert p.allow_legacy_sgpt is False
        assert p.allow_unstructured_text_as_execution is False
        assert STRATEGY_LEGACY_SGPT not in p.effective_strategy_order()

    def test_legacy_sgpt_in_order_without_allow_raises(self):
        with pytest.raises(ValueError, match="legacy_sgpt"):
            ProposePolicy(
                strategy_order=[STRATEGY_DETERMINISTIC_HANDLER, STRATEGY_LEGACY_SGPT],
                allow_legacy_sgpt=False,
            )

    def test_legacy_sgpt_allowed_with_explicit_flag(self):
        p = ProposePolicy(
            strategy_order=[STRATEGY_DETERMINISTIC_HANDLER, STRATEGY_LEGACY_SGPT],
            allow_legacy_sgpt=True,
        )
        assert STRATEGY_LEGACY_SGPT in p.effective_strategy_order()

    def test_unstructured_text_as_execution_requires_admin_override(self):
        with pytest.raises(ValueError, match="requires_admin_override"):
            ProposePolicy(allow_unstructured_text_as_execution=True)

    def test_unstructured_text_allowed_with_admin_override(self):
        p = ProposePolicy(
            allow_unstructured_text_as_execution=True,
            admin_overrides={"allow_unsafe_strategies": True},
        )
        assert p.allow_unstructured_text_as_execution is True

    def test_invalid_llm_mode_rejected(self):
        with pytest.raises(ValueError, match="invalid_llm_mode"):
            ProposePolicy(llm_mode="turbo_mode")

    def test_invalid_on_parse_error_rejected(self):
        with pytest.raises(ValueError, match="invalid_on_parse_error"):
            ProposePolicy(on_parse_error="ignore")

    def test_invalid_on_all_strategies_declined_rejected(self):
        with pytest.raises(ValueError, match="invalid_on_all_strategies_declined"):
            ProposePolicy(on_all_strategies_declined="retry")

    def test_effective_strategy_order_filters_sgpt(self):
        p = ProposePolicy(
            strategy_order=[STRATEGY_DETERMINISTIC_HANDLER, STRATEGY_LEGACY_SGPT],
            allow_legacy_sgpt=True,
        )
        p2 = build_policy_from_dict({
            "strategy_order": [STRATEGY_DETERMINISTIC_HANDLER],
            "allow_legacy_sgpt": False,
        })
        assert STRATEGY_LEGACY_SGPT not in p2.effective_strategy_order()

    def test_to_dict_contains_schema(self):
        p = ProposePolicy()
        d = p.to_dict()
        assert d["schema"] == "propose_policy.v1"
        assert d["allow_legacy_sgpt"] is False


# ── ProposePolicyService ───────────────────────────────────────────────────────

class TestProposePolicyService:
    def _svc(self) -> ProposePolicyService:
        return ProposePolicyService()

    def test_default_policy_returned_when_no_overrides(self):
        svc = self._svc()
        p = svc.get_effective_policy()
        assert isinstance(p, ProposePolicy)
        assert p.allow_legacy_sgpt is False

    def test_project_policy_overrides_default(self):
        svc = self._svc()
        p = svc.get_effective_policy(
            project_config={"propose_policy": {"max_strategy_attempts": 3}}
        )
        assert p.max_strategy_attempts == 3

    def test_blueprint_role_overrides_project(self):
        svc = self._svc()
        p = svc.get_effective_policy(
            project_config={"propose_policy": {"max_strategy_attempts": 2}},
            blueprint_role_config={"propose_policy": {"max_strategy_attempts": 5}},
        )
        assert p.max_strategy_attempts == 5

    def test_task_kind_new_software_project_preset(self):
        svc = self._svc()
        p = svc.get_effective_policy(task_kind="new_software_project")
        assert p.requires_executable_step is True
        assert STRATEGY_LEGACY_SGPT not in p.effective_strategy_order()

    def test_task_kind_research_no_exec_required(self):
        svc = self._svc()
        p = svc.get_effective_policy(task_kind="research")
        assert p.requires_executable_step is False

    def test_task_kind_coding_preset(self):
        svc = self._svc()
        p = svc.get_effective_policy(task_kind="coding")
        assert STRATEGY_DETERMINISTIC_HANDLER in p.effective_strategy_order()
        assert p.requires_executable_step is True

    def test_unknown_task_kind_uses_defaults(self):
        svc = self._svc()
        p = svc.get_effective_policy(task_kind="exotic_custom_task")
        assert p.strategy_order == list(SAFE_DEFAULT_STRATEGY_ORDER)

    def test_invalid_strategy_in_project_config_raises(self):
        svc = self._svc()
        with pytest.raises(Exception):
            svc.get_effective_policy(
                task_kind="coding",
                project_config={"propose_policy": {"llm_mode": "turbo_go_fast"}},
            )
