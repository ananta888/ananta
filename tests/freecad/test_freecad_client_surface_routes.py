from __future__ import annotations


def test_freecad_client_surface_health_and_capabilities(client, admin_auth_header) -> None:
    health = client.get("/api/client-surfaces/freecad/health", headers=admin_auth_header)
    capabilities = client.get("/api/client-surfaces/freecad/capabilities", headers=admin_auth_header)

    assert health.status_code == 200
    assert health.json["data"]["status"] == "connected"
    assert capabilities.status_code == 200
    assert any(item["capability_id"] == "freecad.model.inspect" for item in capabilities.json["data"]["capabilities"])


def test_freecad_client_surface_goal_submit_and_macro_paths(client, admin_auth_header) -> None:
    goal = client.post(
        "/api/client-surfaces/freecad/goals",
        headers=admin_auth_header,
        json={
            "goal": "Inspect this model",
            "context": {"document": {"name": "Doc"}, "objects": [{"name": "Body", "type": "Part"}], "provenance": {"source": "test", "captured_at": "2026-05-01T00:00:00Z"}},
            "capability_id": "freecad.model.inspect",
        },
    )
    export_plan = client.post(
        "/api/client-surfaces/freecad/export-plans",
        headers=admin_auth_header,
        json={"format": "step", "target_path": "/tmp/out.step", "selection_only": True},
    )
    macro_plan = client.post(
        "/api/client-surfaces/freecad/macro-plans",
        headers=admin_auth_header,
        json={"objective": "reduce weight", "context_summary": {"object_count": 3}},
    )
    blocked_execution = client.post(
        "/api/client-surfaces/freecad/macro-executions",
        headers=admin_auth_header,
        json={"payload": {"macro_text": "print('x')"}, "correlation_id": "c-1"},
    )

    assert goal.status_code == 201
    assert goal.json["data"]["status"] == "accepted"
    assert export_plan.status_code == 200
    assert export_plan.json["data"]["plan"]["execution_mode"] == "plan_only"
    assert macro_plan.status_code == 200
    assert macro_plan.json["data"]["plan"]["mode"] == "dry_run"
    assert blocked_execution.status_code == 409
    assert blocked_execution.json["message"] == "approval_required"
