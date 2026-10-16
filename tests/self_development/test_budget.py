"""Operator-selectable session/attempt budgets, including explicit unlimited."""
import json

import pytest

from agent.services.self_development.budget import (
    attempt_budget_exhausted,
    session_active,
    session_deadline,
    session_expired,
    task_budget_exhausted,
    unlimited,
)
from agent.services.self_development.policy import DevelopmentPolicy


def test_unlimited_accepts_zero_and_none_only():
    assert unlimited(0) is True
    assert unlimited(None) is True
    assert unlimited(1) is False
    assert unlimited(50) is False


def test_session_deadline_is_bounded_by_default_and_cap():
    assert session_deadline({}, 1000.0) == 1000.0 + 1800
    assert session_deadline({'max_seconds': 999999}, 1000.0) == 1000.0 + 21600
    assert session_deadline({'max_seconds': 0}, 1000.0) is None
    assert session_deadline({'max_seconds': None}, 1000.0) is None


def test_session_expired_ignores_unlimited_deadline():
    assert session_expired({'expires_at': None}, 10_000.0) is False
    assert session_expired({'expires_at': 100.0}, 100.0) is True
    assert session_expired({'expires_at': 200.0}, 100.0) is False


def test_session_active_unlimited_ignores_stored_deadline():
    session = {'enabled': True, 'expires_at': 100.0}
    assert session_active({'max_seconds': 0}, session, 10_000.0) is True
    assert session_active({'max_seconds': None}, session, 10_000.0) is True
    assert session_active({'max_seconds': 1800}, session, 10_000.0) is False
    assert session_active({'max_seconds': 1800}, {**session, 'enabled': False}, 1.0) is False
    assert session_active({'max_seconds': 1800}, session, 50.0) is True


def test_task_budget_is_unlimited_when_zero_or_null():
    assert task_budget_exhausted({'max_tasks': 0}, {'dispatched': 10_000}) is False
    assert task_budget_exhausted({'max_tasks': None}, {'dispatched': 10_000}) is False
    assert task_budget_exhausted({'max_tasks': 6}, {'dispatched': 6}) is True
    assert task_budget_exhausted({'max_tasks': 6}, {'dispatched': 5}) is False


def test_attempt_budget_is_unlimited_when_zero_or_null():
    assert attempt_budget_exhausted({'max_attempts': 0}, [{}] * 100) is False
    assert attempt_budget_exhausted({'max_attempts': None}, [{}] * 100) is False
    assert attempt_budget_exhausted({'max_attempts': 2}, [{}]) is True
    assert attempt_budget_exhausted({'max_attempts': 2}, []) is False


def _policy(tmp_path, **overrides):
    value = {'enabled': True, 'automatic_publication': True,
        'remote_url': 'https://github.com/ananta888/ananta.git', 'branch': 'ananta/self-development',
        'control_root': str(tmp_path / 'private'), 'workspace_root': str(tmp_path / 'workers'),
        'allowed_roots': ['tests'], 'test_profiles': {'self-development': {'command': 'python -m pytest'}}}
    value.update(overrides)
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(value))
    return DevelopmentPolicy(path)


@pytest.mark.parametrize('max_tasks', [0, None])
def test_policy_accepts_unlimited_task_budget(tmp_path, max_tasks):
    assert _policy(tmp_path, max_tasks=max_tasks).value['max_tasks'] == max_tasks


@pytest.mark.parametrize('max_seconds', [0, None])
def test_policy_accepts_unlimited_session_budget(tmp_path, max_seconds):
    assert _policy(tmp_path, max_seconds=max_seconds).value['max_seconds'] == max_seconds


@pytest.mark.parametrize('max_attempts', [0, None])
def test_policy_accepts_unlimited_attempt_budget(tmp_path, max_attempts):
    assert _policy(tmp_path, max_attempts=max_attempts).value['max_attempts'] == max_attempts


@pytest.mark.parametrize('overrides,reason', [
    ({'max_tasks': 51}, 'development_budget_invalid'),
    ({'max_tasks': -1}, 'development_budget_invalid'),
    ({'max_seconds': -5}, 'development_seconds_budget_invalid'),
    ({'max_attempts': 6}, 'development_attempt_budget_invalid'),
])
def test_policy_still_rejects_out_of_range_values(tmp_path, overrides, reason):
    with pytest.raises(ValueError, match=reason):
        _policy(tmp_path, **overrides)
