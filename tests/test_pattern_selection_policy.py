"""Unit tests for the LLM pattern-selection policy (PAT-007).

Covers:
- default allow-list by task_kind
- risky pattern opt-in (default-deny for risky)
- catalogue-aware validation
- policy overrides
- audit payload shape
- default singleton getter
"""

from __future__ import annotations

import pytest

from agent.services.pattern_selection_policy import (
    DEFAULT_ALLOWLIST,
    RISKY_PATTERN_IDS,
    PatternSelectionPolicy,
    get_pattern_selection_policy,
)


@pytest.fixture
def policy() -> PatternSelectionPolicy:
    return PatternSelectionPolicy()


def test_decide_no_pattern_proposed_is_allowed(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(pattern_id=None, task_kind="coding")
    assert d.allowed is True
    assert d.pattern_id is None
    assert d.risk_level == "low"


def test_decide_unknown_task_kind_falls_back_to_empty_allowlist(
    policy: PatternSelectionPolicy,
) -> None:
    d = policy.decide(pattern_id="strategy", task_kind="nonsense_kind")
    assert d.allowed is False
    assert d.blocked_reason is not None
    assert "not in the default allow-list" in d.blocked_reason


def test_decide_strategy_allowed_for_coding(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(pattern_id="strategy", task_kind="coding")
    assert d.allowed is True
    assert d.risk_level == "medium"
    assert d.audit["allowlist_size"] > 0


def test_decide_factory_method_blocked_for_security(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(pattern_id="factory_method", task_kind="security")
    assert d.allowed is False


def test_decide_proxy_allowed_for_security(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(pattern_id="proxy", task_kind="security")
    assert d.allowed is True


def test_decide_risky_pattern_blocked_without_opt_in(policy: PatternSelectionPolicy) -> None:
    # Use a policy where singleton_guarded is on the coding allowlist,
    # so the only thing blocking it is the risky-pattern gate.
    permissive = PatternSelectionPolicy(
        allowlist={"coding": {"singleton_guarded"}},
    )
    d = permissive.decide(pattern_id="singleton_guarded", task_kind="coding")
    assert d.allowed is False
    assert d.blocked_reason is not None
    assert "risky" in d.blocked_reason.lower()
    assert d.risk_level == "high"


def test_decide_risky_pattern_allowed_with_opt_in(policy: PatternSelectionPolicy) -> None:
    permissive = PatternSelectionPolicy(
        allowlist={"coding": {"singleton_guarded"}},
    )
    d = permissive.decide(
        pattern_id="singleton_guarded",
        task_kind="coding",
        allow_risky_patterns=True,
    )
    assert d.allowed is True
    assert d.risk_level == "high"


def test_decide_unknown_catalogue_id_rejected(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(
        pattern_id="factory_method",
        task_kind="coding",
        catalogue_ids={"strategy", "state"},
    )
    assert d.allowed is False
    assert d.blocked_reason is not None
    assert "not in the catalogue" in d.blocked_reason


def test_decide_known_catalogue_id_passes(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(
        pattern_id="strategy",
        task_kind="coding",
        catalogue_ids={"strategy", "state"},
    )
    assert d.allowed is True


def test_policy_overrides_replace_defaults() -> None:
    custom = PatternSelectionPolicy(
        allowlist={"coding": {"only_one_pattern"}},
        risky_pattern_ids=set(),
    )
    blocked = custom.decide(pattern_id="strategy", task_kind="coding")
    assert blocked.allowed is False
    allowed = custom.decide(pattern_id="only_one_pattern", task_kind="coding")
    assert allowed.allowed is True


def test_decision_to_dict_is_serializable(policy: PatternSelectionPolicy) -> None:
    d = policy.decide(pattern_id="strategy", task_kind="coding")
    blob = d.to_dict()
    assert blob["allowed"] is True
    assert blob["pattern_id"] == "strategy"
    assert blob["task_kind"] == "coding"
    assert isinstance(blob["audit"], dict)


def test_get_pattern_selection_policy_singleton() -> None:
    a = get_pattern_selection_policy()
    b = get_pattern_selection_policy()
    assert a is b


def test_default_allowlist_keys_match_task_kinds() -> None:
    assert set(DEFAULT_ALLOWLIST) >= {"coding", "refactoring", "security"}


def test_risky_pattern_ids_include_singleton_guarded() -> None:
    assert "singleton_guarded" in RISKY_PATTERN_IDS


def test_allowlist_introspection_is_frozen(policy: PatternSelectionPolicy) -> None:
    snap = policy.allowlist()
    assert isinstance(snap["coding"], frozenset)
    # Mutating the policy must not change the snapshot
    snap["coding"] = frozenset({"new"})
    assert "strategy" in policy.allowlist()["coding"]
