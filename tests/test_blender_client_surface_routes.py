from __future__ import annotations


def test_blender_client_surface_health_capabilities_and_goal(client, admin_auth_header) -> None:
    health = client.get("/api/client-surfaces/blender/health", headers=admin_auth_header)
    capabilities = client.get("/api/client-surfaces/blender/capabilities", headers=admin_auth_header)
    goal = client.post(
        "/api/client-surfaces/blender/goals",
        headers=admin_auth_header,
        json={
            "goal": "Inspect scene materials",
            "context": {
                "schema": "blender_scene_context.v1",
                "scene_name": "Scene",
                "selection": ["Cube"],
                "objects": [{"name": "Cube", "type": "MESH", "selected": True, "visible": True}],
                "provenance": {
                    "capture": "addon",
                    "bounded": True,
                    "captured_at": "2026-05-01T00:00:00Z",
                    "object_count_total": 1,
                    "object_count_included": 1,
                },
            },
            "capability_id": "blender.scene.read",
        },
    )

    assert health.status_code == 200
    assert health.json["data"]["surface"] == "blender"
    assert capabilities.status_code == 200
    assert any(item["capability_id"] == "blender.scene.read" for item in capabilities.json["data"]["capabilities"])
    assert goal.status_code == 201
    assert goal.json["data"]["status"] == "accepted"
    assert goal.json["data"]["task_id"]


def test_blender_client_surface_plans_and_approval_gate(client, admin_auth_header) -> None:
    approvals = client.get("/api/client-surfaces/blender/approvals", headers=admin_auth_header)
    export_plan = client.post(
        "/api/client-surfaces/blender/export-plans",
        headers=admin_auth_header,
        json={"format": "gltf", "target_path": "/tmp/scene.gltf", "selection_only": True},
    )
    render_plan = client.post(
        "/api/client-surfaces/blender/render-plans",
        headers=admin_auth_header,
        json={"kind": "preview_render", "width": 512, "height": 512, "samples": 8},
    )
    mutation_plan = client.post(
        "/api/client-surfaces/blender/mutation-plans",
        headers=admin_auth_header,
        json={"action": "rename", "targets": ["Cube"], "capability_id": "blender.scene.mutate"},
    )
    blocked_execution = client.post(
        "/api/client-surfaces/blender/executions",
        headers=admin_auth_header,
        json={"action": "rename", "payload": {"name": "Cube.001"}},
    )
    decision = client.post(
        "/api/client-surfaces/blender/approvals/decision",
        headers=admin_auth_header,
        json={"approval_id": "approval:test", "decision": "approve"},
    )

    assert approvals.status_code == 200
    assert export_plan.status_code == 200
    assert export_plan.json["data"]["plan"]["execution_mode"] == "plan_only"
    assert render_plan.status_code == 200
    assert render_plan.json["data"]["plan"]["approval_required"] is False
    assert mutation_plan.status_code == 200
    assert mutation_plan.json["data"]["plan"]["approval_state"] == "required"
    assert blocked_execution.status_code == 409
    assert blocked_execution.json["message"] == "approval_required"
    assert decision.status_code == 200
    assert decision.json["data"]["decision"] == "approve"
