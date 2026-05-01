from __future__ import annotations

from client_surfaces.blender.addon.context import capture_bounded_scene_context


def test_blender_runtime_golden_path(client, admin_auth_header) -> None:
    context = capture_bounded_scene_context("Scene", [{"name": "Cube", "type": "MESH", "selected": True}], max_objects=8)
    health = client.get("/api/client-surfaces/blender/health", headers=admin_auth_header)
    capabilities = client.get("/api/client-surfaces/blender/capabilities", headers=admin_auth_header)
    goal = client.post(
        "/api/client-surfaces/blender/goals",
        headers=admin_auth_header,
        json={"goal": "Prepare safe scene export", "context": context, "capability_id": "blender.export.plan"},
    )
    tasks = client.get("/api/client-surfaces/blender/tasks", headers=admin_auth_header)
    artifacts = client.get("/api/client-surfaces/blender/artifacts", headers=admin_auth_header)
    approvals = client.get("/api/client-surfaces/blender/approvals", headers=admin_auth_header)
    blocked_execution = client.post(
        "/api/client-surfaces/blender/executions",
        headers=admin_auth_header,
        json={"action": "export", "payload": {"format": "GLTF"}},
    )
    approval = client.post(
        "/api/client-surfaces/blender/approvals/decision",
        headers=admin_auth_header,
        json={"approval_id": "approval:e2e", "decision": "approve"},
    )
    accepted_execution = client.post(
        "/api/client-surfaces/blender/executions",
        headers=admin_auth_header,
        json={"approval_id": "approval:e2e", "action": "export", "payload": {"format": "GLTF"}},
    )

    assert health.status_code == 200
    assert capabilities.status_code == 200
    assert goal.status_code == 201
    assert tasks.status_code == 200
    assert artifacts.status_code == 200
    assert approvals.status_code == 200
    assert blocked_execution.status_code == 409
    assert approval.status_code == 200
    assert accepted_execution.status_code == 200
