"""
Unit tests for orchestration_policy module.

These tests verify delegation rules without requiring HTTP or database layer.
"""

import time

import pytest

from agent.services.task_orchestration_service import build_copilot_routing_prompt, extract_copilot_routing_hint
from agent.routes.tasks.orchestration_policy import (
    DelegationPolicy,
    build_orchestration_read_model,
    build_dispatch_queue,
    choose_worker_for_task,
    compute_retry_delay_seconds,
    compute_lease_expiry,
    derive_required_capabilities,
    evaluate_worker_routing_policy,
    extract_active_lease,
    normalize_worker_roles,
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


class TestWorkerCapabilitySelection:
    def test_normalize_worker_roles_filters_unknown(self):
        assert normalize_worker_roles(["Planner", "unknown", "tester"]) == ["planner", "tester"]

    def test_derive_required_capabilities_from_task_kind(self):
        task = {"title": "Write tests", "description": "Add regression coverage"}
        assert derive_required_capabilities(task, "testing") == ["testing"]

    def test_derive_required_capabilities_for_repo_research(self):
        task = {"title": "Repository research", "description": "Investigate the codebase and git history systematically"}
        assert derive_required_capabilities(task, "research") == ["research", "repo_research"]

    def test_choose_worker_prefers_specialized_research_capability(self):
        workers = [
            {
                "url": "http://generic-researcher:5000",
                "status": "online",
                "worker_roles": ["researcher"],
                "capabilities": ["research"],
            },
            {
                "url": "http://repo-researcher:5000",
                "status": "online",
                "worker_roles": ["researcher"],
                "capabilities": ["research", "repo_research"],
            },
        ]
        selection = choose_worker_for_task(
            {"title": "Repository research", "description": "Investigate the codebase and repo history"},
            workers,
            task_kind="research",
        )
        assert selection.worker_url == "http://repo-researcher:5000"
        assert "repo_research" in selection.matched_capabilities

    def test_choose_worker_by_capabilities(self):
        workers = [
            {"url": "http://coder:5000", "status": "online", "worker_roles": ["coder"], "capabilities": ["coding"]},
            {"url": "http://tester:5000", "status": "online", "worker_roles": ["tester"], "capabilities": ["testing"]},
        ]
        selection = choose_worker_for_task({"title": "Run tests"}, workers, task_kind="testing")
        assert selection.worker_url == "http://tester:5000"
        assert selection.strategy in {"capability_match", "capability_quality_load_match"}

    def test_choose_worker_falls_back_to_online_worker(self):
        workers = [
            {"url": "http://generic:5000", "status": "online", "worker_roles": [], "capabilities": []},
        ]
        selection = choose_worker_for_task({"title": "Unknown task"}, workers, task_kind="ops")
        assert selection.worker_url == "http://generic:5000"
        assert selection.strategy == "fallback"

    def test_choose_worker_skips_unvalidated_or_saturated_workers(self):
        workers = [
            {
                "url": "http://blocked:5000",
                "status": "online",
                "registration_validated": False,
                "worker_roles": ["tester"],
                "capabilities": ["testing"],
            },
            {
                "url": "http://busy:5000",
                "status": "online",
                "registration_validated": True,
                "worker_roles": ["tester"],
                "capabilities": ["testing"],
                "execution_limits": {"max_parallel_tasks": 1},
                "current_load": 1,
            },
            {
                "url": "http://ok:5000",
                "status": "online",
                "registration_validated": True,
                "worker_roles": ["tester"],
                "capabilities": ["testing"],
                "execution_limits": {"max_parallel_tasks": 2},
                "current_load": 0,
            },
        ]
        selection = choose_worker_for_task({"title": "Run tests"}, workers, task_kind="testing")
        assert selection.worker_url == "http://ok:5000"

    def test_build_dispatch_queue_orders_high_priority_first(self):
        queue = build_dispatch_queue(
            [
                {"id": "t-low", "status": "todo", "priority": "Low", "created_at": 1},
                {"id": "t-high", "status": "todo", "priority": "High", "created_at": 2},
                {"id": "t-mid", "status": "assigned", "priority": "Medium", "created_at": 0},
            ]
        )
        assert [item["task_id"] for item in queue] == ["t-high", "t-mid", "t-low"]

    def test_compute_retry_delay_is_bounded(self):
        delay = compute_retry_delay_seconds(3, 0.5, max_backoff_seconds=1.0, jitter_factor=0.0)
        assert delay == 1.0

    def test_evaluate_worker_routing_policy_returns_blocked_decision(self, db_session):
        selection, decision = evaluate_worker_routing_policy(
            task={"id": "t-none", "title": "No worker"},
            workers=[],
            decision_type="assignment",
            task_kind="testing",
            required_capabilities=["testing"],
            task_id="t-none",
        )
        assert selection.worker_url is None
        assert decision.status == "blocked"


class TestHubCopilotRoutingHints:
    def test_extract_copilot_routing_hint_accepts_known_worker(self):
        hint = extract_copilot_routing_hint(
            '{"suggested_worker_url":"http://tester:5000","reasoning":"best fit","confidence":0.82}',
            ["http://tester:5000", "http://coder:5000"],
        )
        assert hint is not None
        assert hint["suggested_worker_url"] == "http://tester:5000"
        assert hint["reasoning"] == "best fit"
        assert hint["confidence"] == pytest.approx(0.82)

    def test_extract_copilot_routing_hint_rejects_unknown_worker(self):
        hint = extract_copilot_routing_hint(
            '{"suggested_worker_url":"http://unknown:5000","reasoning":"maybe","confidence":1.7}',
            ["http://tester:5000"],
        )
        assert hint is not None
        assert hint["suggested_worker_url"] is None
        assert hint["confidence"] == 1.0

    def test_build_copilot_routing_prompt_contains_task_and_worker_context(self):
        prompt = build_copilot_routing_prompt(
            task={"id": "t1", "title": "Run tests", "description": "Add regression coverage"},
            task_kind="testing",
            required_capabilities=["testing"],
            workers=[
                {
                    "url": "http://tester:5000",
                    "status": "online",
                    "worker_roles": ["tester"],
                    "capabilities": ["testing"],
                    "current_load": 0,
                    "execution_limits": {"max_parallel_tasks": 2},
                }
            ],
        )
        assert '"task_kind": "testing"' in prompt
        assert '"suggested_worker_url": "string|null"' in prompt
        assert '"url": "http://tester:5000"' in prompt


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
