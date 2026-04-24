from unittest.mock import patch

from agent.services.tool_routing_service import get_tool_routing_service


def test_tool_routing_builds_normalized_capability_catalog():
    service = get_tool_routing_service()
    fake_backends = {
        "capabilities": {
            "opencode": {"available": True},
            "aider": {"available": False},
        },
    }
    cfg = {
        "llm_tool_guardrails": {
            "tool_classes": {
                "create_team": "write",
                "set_autopilot_state": "admin",
                "list_teams": "read",
            }
        },
        "cli_session_mode": {"stateful_backends": ["opencode"]},
    }

    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        catalog = service.build_capability_catalog(agent_cfg=cfg)

    assert catalog["catalog_version"] == "tool-router-v1"
    summary = catalog["summary"]
    assert summary["backend_count"] == 2
    assert summary["tool_count"] == 3
    assert "patching" in summary["capability_classes"]
    opencode = next(item for item in catalog["items"] if item["id"] == "opencode" and item["kind"] == "backend")
    assert opencode["supports_stateful_session"] is True
    assert opencode["availability"] == "ready"
    admin_tool = next(item for item in catalog["items"] if item["id"] == "set_autopilot_state" and item["kind"] == "tool")
    assert admin_tool["risk_class"] == "high"
    assert admin_tool["requires_approval"] is True


def test_tool_routing_decision_explains_alternatives():
    service = get_tool_routing_service()
    fake_backends = {
        "capabilities": {
            "opencode": {"available": True},
            "aider": {"available": True},
            "sgpt": {"available": True},
        },
    }
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        routed = service.route_execution_backend(
            task_kind="coding",
            requested_backend="opencode",
            required_capabilities=["patching"],
            governance_mode="balanced",
            agent_cfg={},
        )

    decision = routed["decision"]
    assert decision["selected_target"] == "opencode"
    assert decision["selected_reason"] == "requested_backend_selected"
    alternatives = decision["alternatives"]
    assert any(item["target"] == "opencode" and item["selected"] for item in alternatives)
    assert any(item["target"] == "sgpt" and "missing_capabilities" in item for item in alternatives)


def test_tool_routing_includes_specialized_profile_and_can_select_it():
    service = get_tool_routing_service()
    fake_backends = {"capabilities": {"opencode": {"available": True}, "sgpt": {"available": True}}}
    cfg = {
        "specialized_worker_profiles": {
            "enabled": True,
            "profiles": {
                "ml_intern": {
                    "enabled": True,
                    "capability_classes": ["ml_research", "research"],
                    "risk_class": "medium",
                    "requires_approval": True,
                    "available": True,
                }
            },
        }
    }
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        catalog = service.build_capability_catalog(agent_cfg=cfg)
        routed = service.route_execution_backend(
            task_kind="research",
            requested_backend="ml_intern",
            required_capabilities=["ml_research"],
            governance_mode="balanced",
            agent_cfg=cfg,
        )

    specialized = next(item for item in catalog["items"] if item["id"] == "ml_intern")
    assert specialized["kind"] == "specialized_backend"
    assert specialized["availability"] == "ready"
    assert routed["decision"]["selected_target"] == "ml_intern"
