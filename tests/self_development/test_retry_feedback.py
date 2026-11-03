"""Retry feedback transport and policy budget extensions."""

import json

import pytest

from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.retries import _review_feedback, describe_for_retry


def _policy(tmp_path, extra=None):
    value = {
        "enabled": True,
        "automatic_publication": True,
        "remote_url": "https://github.com/ananta888/ananta.git",
        "branch": "ananta/self-development",
        "control_root": str(tmp_path / "control"),
        "workspace_root": str(tmp_path / "workspaces"),
        "tenant_id": "local-operator",
        "team_id": "074cd040-7f55-4dd5-a9b1-67209ec07038",
        "test_profiles": {"self-development": {"command": "python -m pytest tests/self_development"}},
    }
    value.update(extra or {})
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return DevelopmentPolicy(path)


def test_policy_accepts_bounded_max_concurrency_and_attempt_reset(tmp_path):
    policy = _policy(tmp_path, {"max_concurrency": 2, "attempt_reset": True})
    assert policy.max_concurrency() == 2
    assert policy.value["attempt_reset"] is True


def test_policy_defaults_to_single_concurrency(tmp_path):
    assert _policy(tmp_path).max_concurrency() == 1


def test_policy_rejects_invalid_concurrency_and_reset_flag(tmp_path):
    for extra in ({"max_concurrency": 0}, {"max_concurrency": 9}, {"attempt_reset": "yes"}):
        with pytest.raises(ValueError):
            _policy(tmp_path, extra)


def test_review_feedback_captures_reason_and_verdict_evidence():
    item = {
        "reason": "acceptance_review_failed:acceptance_criteria_not_satisfied",
        "acceptance_review_evidence": {
            "reason": "acceptance_review_failed:x",
            "decided_by": "local_decision",
            "answers": {"criterion_0": {"probability": 0.4}},
        },
    }
    feedback = _review_feedback(item, 2)
    assert feedback["attempt"] == 2
    assert feedback["reason"].startswith("acceptance_review_failed")
    assert feedback["decided_by"] == "local_decision"
    assert feedback["verdict_reason"] == "acceptance_review_failed:x"
    assert feedback["answers"]["criterion_0"]["probability"] == 0.4


def test_describe_for_retry_embeds_feedback_bounded():
    item = {
        "description": "Implement the helper.",
        "review_feedback": {"attempt": 3, "reason": "criteria not satisfied"},
    }
    description = describe_for_retry(item)
    assert description.startswith("Implement the helper.")
    assert "Previous review feedback (attempt 3" in description
    assert "criteria not satisfied" in description
    assert len(description) <= 4000


def test_describe_for_retry_without_feedback_is_base():
    assert describe_for_retry({"description": "Base."}) == "Base."
