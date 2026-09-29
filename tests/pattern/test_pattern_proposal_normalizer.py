"""Tests for the pattern-proposal normalizer (PAT-008)."""

from __future__ import annotations

import pytest

from agent.services.pattern_proposal_normalizer import (
    PatternProposalNormalizer,
    get_pattern_proposal_normalizer,
)


def test_no_proposal_is_accepted() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(proposal=None)
    assert out.accepted is True
    assert out.pattern_id is None
    assert out.audit.get("reason") == "no_pattern_proposed"


def test_valid_proposal_for_coding_strategy_is_accepted() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "strategy",
            "task_kind": "coding",
            "language": "python",
            "parameters_provided": {"context_class": "Order"},
        }
    )
    assert out.accepted is True
    assert out.pattern_id == "strategy"
    assert out.task_kind == "coding"
    assert out.parameters_provided == {"context_class": "Order"}


def test_invalid_proposal_shape_is_rejected() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(proposal="not a dict")  # type: ignore[arg-type]
    assert out.accepted is False
    assert out.blocked_reason is not None
    assert "must be a dict" in out.blocked_reason


def test_proposal_without_pattern_id_is_rejected() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(proposal={"task_kind": "coding"})
    assert out.accepted is False
    assert out.blocked_reason is not None
    assert "pattern_id" in out.blocked_reason


def test_proposal_with_non_dict_parameters_is_rejected() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "strategy",
            "task_kind": "coding",
            "parameters_provided": "oops",
        }
    )
    assert out.accepted is False
    assert out.blocked_reason is not None
    assert "parameters_provided" in out.blocked_reason


def test_proposal_dropping_unknown_keys() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "strategy",
            "task_kind": "coding",
            "language": "python",
            "evil_field": "rm -rf /",
            "another_one": 42,
        }
    )
    # Even with extras, the proposal is accepted because the
    # structural + policy checks still pass. The audit log records
    # the dropped keys.
    assert out.accepted is True
    assert "evil_field" in out.audit["dropped_keys"]
    assert "another_one" in out.audit["dropped_keys"]


def test_risky_pattern_blocked_without_opt_in() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={"pattern_id": "singleton_guarded", "task_kind": "coding"}
    )
    # singleton_guarded is not in the default coding allow-list,
    # so the policy blocks it on that ground first.
    assert out.accepted is False
    assert out.blocked_reason is not None


def test_risky_pattern_blocked_with_opt_in_but_not_in_allowlist() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "singleton_guarded",
            "task_kind": "coding",
            "allow_risky": True,
        }
    )
    assert out.accepted is False


def test_catalogue_aware_rejection() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "strategy",
            "task_kind": "coding",
            "language": "python",
        },
        catalogue_ids={"state", "command"},
    )
    assert out.accepted is False
    assert out.blocked_reason is not None
    assert "not in the catalogue" in out.blocked_reason


def test_catalogue_aware_acceptance() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(
        proposal={
            "pattern_id": "strategy",
            "task_kind": "coding",
            "language": "python",
        },
        catalogue_ids={"strategy", "state"},
    )
    assert out.accepted is True


def test_metadata_payload_shape() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(proposal=None)
    blob = out.to_metadata()
    assert blob["accepted"] is True
    assert blob["pattern_id"] is None
    assert "audit" in blob


def test_singleton_normalizer_is_shared() -> None:
    a = get_pattern_proposal_normalizer()
    b = get_pattern_proposal_normalizer()
    assert a is b


def test_policy_audit_is_propagated() -> None:
    n = PatternProposalNormalizer()
    out = n.normalize(proposal={"pattern_id": "strategy", "task_kind": "coding"})
    assert "policy_audit" in out.audit
    assert "allowlist_size" in out.audit["policy_audit"]
