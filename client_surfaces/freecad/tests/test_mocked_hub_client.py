from __future__ import annotations

from client_surfaces.freecad.workbench.approvals import can_execute_locally
from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.commands import submit_freecad_goal
from client_surfaces.freecad.workbench.context import capture_bounded_document_context
from client_surfaces.freecad.workbench.policy import describe_policy_response
from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings


def test_mocked_hub_client_health_capabilities_and_goal_submit() -> None:
    client = FreecadHubClient(FreecadWorkbenchSettings(endpoint="http://localhost:8000", allow_insecure_http=True))
    health = client.health()
    capabilities = client.capabilities()
    context = capture_bounded_document_context({"name": "Doc"}, [{"name": "Body", "type": "Part"}])
    result = submit_freecad_goal(client, goal="Inspect weak constraints", context=context)

    assert health["status"] == "connected"
    assert "freecad.model.inspect" in capabilities["capabilities"]
    assert result["status"] == "accepted"
    assert result["response"]["task_id"] == "fc-task-1"


def test_denied_and_approval_required_states_do_not_enable_local_execution() -> None:
    denied = describe_policy_response({"status": "policy_denied", "reason": "blocked_by_policy"}, capability_id="freecad.macro.execute")
    pending = {"state": "pending", "approval_id": "APR-1"}

    assert denied["ui_state"] == "denied"
    assert can_execute_locally(pending) is False
