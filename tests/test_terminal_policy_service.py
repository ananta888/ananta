from __future__ import annotations

from agent.services.terminal_policy_service import TerminalPolicyService


def test_worker_permission_does_not_grant_hub_permission():
    svc = TerminalPolicyService()
    user = {"sub": "u1", "role": "user"}

    allow_worker = svc.evaluate(user_ctx=user, operation="attach", target_type="worker", target_id="w1", cfg={})
    deny_hub = svc.evaluate(user_ctx=user, operation="attach", target_type="hub", target_id="hub1", cfg={})

    assert allow_worker.allow is True
    assert deny_hub.allow is False
    assert deny_hub.reason_code == "terminal_hub_access_denied_default"


def test_hub_as_worker_requires_explicit_permission():
    svc = TerminalPolicyService()
    user = {"sub": "u1", "role": "user"}
    cfg = {
        "terminal_policy": {
            "role_permissions": {
                "user": ["terminal.worker.create"],
            }
        }
    }

    denied = svc.evaluate(user_ctx=user, operation="create", target_type="hub_as_worker", target_id="h1", cfg=cfg)
    assert denied.allow is False
    assert denied.reason_code == "terminal_target_blocked_by_sandbox_policy"

    user2 = {"sub": "u2", "role": "admin", "terminal_permissions": ["terminal.hub_as_worker.create"]}
    allowed = svc.evaluate(user_ctx=user2, operation="create", target_type="hub_as_worker", target_id="h1", cfg=cfg)
    assert allowed.allow is False
    assert allowed.reason_code == "terminal_target_blocked_by_sandbox_policy"


def test_hub_as_worker_list_visibility_is_not_blocked_by_sandbox_defaults():
    svc = TerminalPolicyService()
    user = {"sub": "u2", "role": "admin", "terminal_permissions": ["terminal.hub_as_worker.list"]}

    allowed = svc.evaluate(user_ctx=user, operation="list", target_type="hub_as_worker", target_id="h1", cfg={})
    assert allowed.allow is True
    assert allowed.reason_code == "terminal_permission_granted"


def test_denied_and_allowed_decisions_include_stable_metadata():
    svc = TerminalPolicyService()
    denied = svc.evaluate(user_ctx={"sub": "u1", "role": "viewer"}, operation="write", target_type="worker", target_id="w1", cfg={})
    assert denied.decision_id.startswith("term-dec-")
    assert denied.policy_version
    assert denied.reason_code == "terminal_permission_denied"

    allowed = svc.evaluate(user_ctx={"sub": "u1", "role": "admin"}, operation="read", target_type="worker", target_id="w1", cfg={})
    assert allowed.allow is True
    assert allowed.policy_version
    assert allowed.matched_rule_id == "worker.read.allow"


def test_terminal_sandbox_admin_required_for_hub_write_like_actions():
    svc = TerminalPolicyService()
    cfg = {
        "sandbox_policy": {
            "terminal_access": {
                "enforce": True,
                "blocked_target_types": [],
                "write_requires_admin_for": ["hub"],
            }
        }
    }
    denied = svc.evaluate(
        user_ctx={"sub": "u1", "role": "user", "terminal_permissions": ["terminal.hub.attach"]},
        operation="attach",
        target_type="hub",
        target_id="hub1",
        cfg=cfg,
    )
    assert denied.allow is False
    assert denied.reason_code == "terminal_sandbox_admin_required"
