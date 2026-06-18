from __future__ import annotations

from agent.services.hub_worker_graph_service import HubWorkerGraphService


def test_hub_worker_graph_is_hub_centered_and_uses_configured_workers() -> None:
    graph = HubWorkerGraphService().build(user_config={
        "chat_backend": "ananta-worker",
        "opencode_runtime": {"execution_mode": "live_terminal"},
        "hermes_worker_adapter": {"enabled": True},
        "hub_worker_routing": {"review": "hermes"},
    })

    assert graph["schema"] == "ananta.hub_worker_graph.v1"
    assert "hub::ananta" in graph["nodes"]
    assert "worker_instance::ananta-worker" in graph["nodes"]
    assert "worker_instance::opencode" in graph["nodes"]
    assert "worker_instance::hermes" in graph["nodes"]
    assert any(edge["edge_type"] == "controls_worker" for edge in graph["edges"])
    assert any(
        edge["edge_type"] == "routes_task_to_worker"
        and edge["data"].get("task_kind") == "review"
        and edge["data"].get("preferred_worker") == "hermes"
        for edge in graph["edges"]
    )


def test_hub_worker_graph_does_not_create_worker_to_worker_edges() -> None:
    graph = HubWorkerGraphService().build(user_config={
        "opencode_runtime": {"execution_mode": "live_terminal"},
    })

    worker_ids = {
        node_id
        for node_id, node in graph["nodes"].items()
        if node["node_type"] == "worker_instance"
    }
    assert not any(
        edge["source"] in worker_ids and edge["target"] in worker_ids
        for edge in graph["edges"]
    )


def test_hub_worker_graph_includes_taskflows_and_fallback_chain() -> None:
    graph = HubWorkerGraphService().build(user_config={
        "opencode_runtime": {"execution_mode": "live_terminal"},
        "routing_fallback_policy": {
            "fallback_order": ["opencode", "ananta-worker"],
        },
        "hub_worker_taskflows": {
            "repair": [
                {"name": "Planner", "worker": "ananta-worker"},
                {"name": "Developer", "worker": "opencode"},
            ],
        },
    })

    assert "fallback_chain::worker-routing" in graph["nodes"]
    assert "taskflow::repair" in graph["nodes"]
    assert "taskflow_step::repair::1" in graph["nodes"]
    assert any(edge["edge_type"] == "hands_off_artifact_to" for edge in graph["edges"])
