"""
Unit tests for orchestration_policy module.

These tests verify delegation rules without requiring HTTP or database layer.
"""

import time
import pytest

from agent.routes.tasks.orchestration_policy import (
    DelegationPolicy,
    LeaseInfo,
    extract_active_lease,
    compute_lease_expiry,
    build_orchestration_read_model,
)


class MockRoleProvider:
    """Mock role provider for testing."""

    def __init__(self, role: str | None):
        self._role = role

    @property
    def role(self) -> str | None:
        return self._role


class TestDelegationPolicy:
    """Tests for DelegationPolicy class."""

    def test_delegation_allowed_for_hub_role(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"), required_role="hub")
        assert policy.check_delegation_allowed() is None

    def test_delegation_denied_for_worker_role(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="worker"), required_role="hub")
        assert policy.check_delegation_allowed() == "hub_role_required"

    def test_delegation_denied_for_none_role(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role=None), required_role="hub")
        assert policy.check_delegation_allowed() == "hub_role_required"

    def test_delegation_denied_without_role_provider(self):
        policy = DelegationPolicy(role_provider=None)
        assert policy.check_delegation_allowed() == "no_role_provider"

    def test_validate_lease_duration_normal(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        assert policy.validate_lease_duration(120) == 120

    def test_validate_lease_duration_clamp_min(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        assert policy.validate_lease_duration(5) == 10

    def test_validate_lease_duration_clamp_max(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        assert policy.validate_lease_duration(5000) == 3600

    def test_can_claim_task_no_lease(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        task = {"history": []}
        can_claim, error = policy.can_claim_task(task, "http://agent1:5000")
        assert can_claim is True
        assert error is None

    def test_can_claim_task_same_agent_lease(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        future = time.time() + 300
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": future,
                    },
                }
            ]
        }
        can_claim, error = policy.can_claim_task(task, "http://agent1:5000")
        assert can_claim is True
        assert error is None

    def test_can_claim_task_other_agent_lease(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        future = time.time() + 300
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": future,
                    },
                }
            ]
        }
        can_claim, error = policy.can_claim_task(task, "http://agent2:5000")
        assert can_claim is False
        assert error == "task_already_leased"

    def test_can_claim_task_expired_lease(self):
        policy = DelegationPolicy(role_provider=MockRoleProvider(role="hub"))
        past = time.time() - 100
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": past,
                    },
                }
            ]
        }
        can_claim, error = policy.can_claim_task(task, "http://agent2:5000")
        assert can_claim is True
        assert error is None


class TestExtractActiveLease:
    """Tests for extract_active_lease function."""

    def test_no_lease_with_empty_history(self):
        task = {"history": []}
        assert extract_active_lease(task) is None

    def test_no_lease_with_expired_lease(self):
        past = time.time() - 100
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": past,
                    },
                }
            ]
        }
        assert extract_active_lease(task) is None

    def test_active_lease_returned(self):
        future = time.time() + 300
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": future,
                        "idempotency_key": "key-123",
                    },
                }
            ]
        }
        lease = extract_active_lease(task)
        assert lease is not None
        assert lease.agent_url == "http://agent1:5000"
        assert lease.lease_until == future
        assert lease.idempotency_key == "key-123"

    def test_most_recent_lease_returned(self):
        future1 = time.time() + 200
        future2 = time.time() + 400
        task = {
            "history": [
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": future1,
                    },
                },
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent2:5000",
                        "lease_until": future2,
                    },
                },
            ]
        }
        lease = extract_active_lease(task)
        assert lease is not None
        assert lease.agent_url == "http://agent2:5000"

    def test_skips_non_claim_events(self):
        future = time.time() + 300
        task = {
            "history": [
                {"event_type": "task_created"},
                {"event_type": "task_updated"},
                {
                    "event_type": "task_claimed",
                    "details": {
                        "agent_url": "http://agent1:5000",
                        "lease_until": future,
                    },
                },
            ]
        }
        lease = extract_active_lease(task)
        assert lease is not None


class TestComputeLeaseExpiry:
    """Tests for compute_lease_expiry function."""

    def test_lease_expiry_in_future(self):
        now = time.time()
        expiry = compute_lease_expiry(120)
        assert expiry > now
        assert expiry == pytest.approx(now + 120, rel=0.01)

    def test_lease_expiry_for_zero_seconds(self):
        now = time.time()
        expiry = compute_lease_expiry(0)
        assert expiry == pytest.approx(now, rel=0.01)


class TestBuildOrchestrationReadModel:
    """Tests for build_orchestration_read_model function."""

    def test_empty_tasks(self):
        result = build_orchestration_read_model([])
        assert result["queue"] == {
            "todo": 0,
            "assigned": 0,
            "in_progress": 0,
            "blocked": 0,
            "completed": 0,
            "failed": 0,
        }
        assert result["by_agent"] == {}
        assert result["active_leases"] == []
        assert result["recent_tasks"] == []

    def test_counts_tasks_by_status(self):
        tasks = [
            {"status": "todo", "updated_at": 100},
            {"status": "todo", "updated_at": 101},
            {"status": "completed", "updated_at": 102},
            {"status": "failed", "updated_at": 103},
        ]
        result = build_orchestration_read_model(tasks)
        assert result["queue"]["todo"] == 2
        assert result["queue"]["completed"] == 1
        assert result["queue"]["failed"] == 1

    def test_counts_by_assigned_agent(self):
        tasks = [
            {"status": "assigned", "assigned_agent_url": "http://agent1:5000", "updated_at": 100},
            {"status": "assigned", "assigned_agent_url": "http://agent1:5000", "updated_at": 101},
            {"status": "assigned", "assigned_agent_url": "http://agent2:5000", "updated_at": 102},
        ]
        result = build_orchestration_read_model(tasks)
        assert result["by_agent"]["http://agent1:5000"] == 2
        assert result["by_agent"]["http://agent2:5000"] == 1

    def test_recent_tasks_limited_to_40(self):
        tasks = [{"id": f"task-{i}", "status": "todo", "updated_at": i} for i in range(50)]
        result = build_orchestration_read_model(tasks)
        assert len(result["recent_tasks"]) == 40
        assert result["recent_tasks"][0]["id"] == "task-49"

    def test_includes_active_leases(self):
        future = time.time() + 300
        tasks = [
            {
                "id": "task-1",
                "status": "assigned",
                "updated_at": 100,
                "history": [
                    {
                        "event_type": "task_claimed",
                        "details": {
                            "agent_url": "http://agent1:5000",
                            "lease_until": future,
                        },
                    }
                ],
            }
        ]
        result = build_orchestration_read_model(tasks)
        assert len(result["active_leases"]) == 1
        assert result["active_leases"][0]["task_id"] == "task-1"
        assert result["active_leases"][0]["agent_url"] == "http://agent1:5000"
