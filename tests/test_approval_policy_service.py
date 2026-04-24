from agent.services.approval_policy_service import get_approval_policy_service


def test_approval_policy_blocks_admin_mutation():
    service = get_approval_policy_service()
    cfg = {
        "llm_tool_guardrails": {"tool_classes": {"set_autopilot_state": "admin"}},
        "governance_mode": "balanced",
    }
    decision = service.evaluate(
        command=None,
        tool_calls=[{"name": "set_autopilot_state", "args": {"enabled": False}}],
        task={"id": "approval-1"},
        agent_cfg=cfg,
    )
    payload = decision.as_dict()
    assert payload["classification"] == "blocked"
    assert payload["operation_class"] == "admin_mutation"


def test_approval_policy_confirm_required_can_be_enforced():
    service = get_approval_policy_service()
    cfg = {
        "governance_mode": "strict",
        "unified_approval_policy": {"enabled": True, "enforce_confirm_required": True},
    }
    decision = service.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "approval-2", "approval_confirmed": False},
        agent_cfg=cfg,
    )
    payload = decision.as_dict()
    assert payload["classification"] == "confirm_required"
    assert payload["enforced"] is True
    assert payload["required_confirmation_level"] == "operator"


def test_approval_policy_confirmed_task_is_allowed():
    service = get_approval_policy_service()
    cfg = {
        "governance_mode": "strict",
        "unified_approval_policy": {"enabled": True, "enforce_confirm_required": True},
    }
    decision = service.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "approval-3", "approval_confirmed": True},
        agent_cfg=cfg,
    )
    payload = decision.as_dict()
    assert payload["classification"] == "allow"
    assert payload["reason_code"] == "approval_confirmed_by_operator"


def test_approval_policy_specialized_backend_requires_confirmation():
    service = get_approval_policy_service()
    cfg = {
        "governance_mode": "balanced",
        "unified_approval_policy": {"enabled": True, "enforce_confirm_required": True},
        "specialized_worker_profiles": {
            "enabled": True,
            "profiles": {
                "ml_intern": {
                    "enabled": True,
                    "risk_class": "medium",
                    "requires_approval": True,
                    "routing_aliases": ["ml-intern"],
                }
            },
        },
    }
    decision = service.evaluate(
        command="echo run",
        tool_calls=None,
        task={"id": "approval-specialized", "last_proposal": {"routing": {"effective_backend": "ml_intern"}}},
        agent_cfg=cfg,
    )
    payload = decision.as_dict()
    assert payload["classification"] == "confirm_required"
    assert payload["required_confirmation_level"] == "operator"
    assert payload["details"]["specialized_backend"]["backend_id"] == "ml_intern"
