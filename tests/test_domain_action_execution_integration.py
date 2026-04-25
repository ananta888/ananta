from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch


class _StubRouteResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _StubRouter:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.calls: list[dict[str, Any]] = []

    def route(self, **kwargs) -> _StubRouteResult:  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return _StubRouteResult(self.payload)


def _set_domain_action_task(app, tid: str, proposal_command: str) -> None:
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            description="domain action execution test",
            task_kind="domain_action",
            last_proposal={"command": proposal_command, "reason": "domain action proposal"},
        )


def test_domain_action_execution_routes_without_shell_fallback(client, app, admin_auth_header) -> None:
    tid = "DOMAIN-ACTION-EXEC-1"
    _set_domain_action_task(
        app,
        tid,
        json.dumps(
            {
                "domain_id": "blender",
                "capability_id": "blender.scene.plan.v1",
                "action_id": "plan_scene",
                "execution_mode": "plan",
            }
        ),
    )
    stub_router = _StubRouter(
        {
            "state": "plan",
            "reason": "plan_only",
            "domain_id": "blender",
            "capability_id": "blender.scene.plan.v1",
            "action_id": "plan_scene",
        }
    )

    with (
        patch(
            "agent.services.task_scoped_execution_service.TaskScopedExecutionService._build_domain_action_router",
            return_value=stub_router,
        ),
        patch("agent.shell.PersistentShell.execute") as shell_execute,
    ):
        response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["status"] == "completed"
    routed = json.loads(data["output"])
    assert routed["state"] == "plan"
    assert stub_router.calls and stub_router.calls[0]["domain_id"] == "blender"
    assert shell_execute.call_count == 0


def test_domain_action_execution_maps_approval_required_to_blocked(client, app, admin_auth_header) -> None:
    tid = "DOMAIN-ACTION-EXEC-2"
    _set_domain_action_task(
        app,
        tid,
        json.dumps(
            {
                "domain_id": "blender",
                "capability_id": "blender.scene.plan.v1",
                "action_id": "apply_scene",
                "execution_mode": "execute",
            }
        ),
    )
    stub_router = _StubRouter(
        {
            "state": "approval_required",
            "reason": "missing_approval",
            "domain_id": "blender",
            "capability_id": "blender.scene.plan.v1",
            "action_id": "apply_scene",
        }
    )

    with patch(
        "agent.services.task_scoped_execution_service.TaskScopedExecutionService._build_domain_action_router",
        return_value=stub_router,
    ):
        response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["status"] == "blocked"
    assert data["failure_type"] == "approval_required"
    routed = json.loads(data["output"])
    assert routed["state"] == "approval_required"


def test_domain_action_execution_rejects_invalid_json_command(client, app, admin_auth_header) -> None:
    tid = "DOMAIN-ACTION-EXEC-3"
    _set_domain_action_task(app, tid, "this-is-not-json")

    response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 409
    assert response.json["message"] == "domain_action_payload_invalid"
