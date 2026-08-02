from __future__ import annotations

from unittest.mock import patch

from client_surfaces.operator_tui._renderer_content_artifact import _organization_content_lines
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _state(**updates) -> OperatorState:
    values = {
        "endpoint": "http://hub.local",
        "audit_context": {"token": "user-token"},
        "header_logo_game": {},
    }
    values.update(updates)
    return OperatorState(**values)


def test_org_status_reads_only_hub_apis_and_builds_compact_artifact_projection() -> None:
    with patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type:
        client = client_type.return_value
        client.request_json.side_effect = [
            {"items": [{"id": "org-1", "title": "Medium", "lifecycle": "active", "team_count": 8}]},
            {"items": [{"key": "enterprise-medium", "version": "1", "team_count": 8}]},
        ]
        result = execute_command(":org status", _state())

    assert result.handled is True
    assert result.state.section_id == "artifacts"
    payload = result.state.section_payloads["artifacts"]
    assert payload["organization_mode"] is True
    assert payload["organizations"][0]["team_count"] == 8
    assert [call.args[0] for call in client.request_json.call_args_list] == ["GET", "GET"]


def test_org_validate_keeps_bound_plan_for_separate_confirmed_apply() -> None:
    plan = {
        "blueprint_key": "enterprise-medium",
        "blueprint_version": "1",
        "definition_revision": "revision-1",
        "plan_digest": "digest-1",
        "team_count": 8,
        "unit_count": 12,
        "diagnostics": [],
    }
    with patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type:
        client_type.return_value.request_json.return_value = plan
        result = execute_command(":org validate enterprise-medium --teams 8", _state())

    assert result.handled is True
    assert result.state.header_logo_game["organization_compile_plan"] == plan
    assert result.state.section_payloads["artifacts"]["view"] == "compile"


def test_org_instantiate_requires_explicit_confirmation_and_out_of_band_grant() -> None:
    state = _state(
        header_logo_game={
            "organization_compile_plan": {
                "definition_revision": "revision-1",
                "plan_digest": "digest-1",
            }
        }
    )
    pending = execute_command(":org instantiate", state)

    assert pending.handled is True
    assert "--confirm" in pending.message

    with patch.dict("os.environ", {}, clear=True):
        denied = execute_command(":org instantiate --confirm", state)

    assert denied.handled is False
    assert "ANANTA_ORGANIZATION_ADMIN_GRANT" in denied.message


def test_org_confirmed_apply_uses_same_revision_digest_and_admin_headers() -> None:
    plan = {
        "definition_revision": "revision-1",
        "plan_digest": "digest-1",
        "team_count": 8,
        "title": "Medium",
    }
    state = _state(header_logo_game={"organization_compile_plan": plan})
    with (
        patch.dict("os.environ", {"ANANTA_ORGANIZATION_ADMIN_GRANT": "grant-value"}, clear=False),
        patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type,
    ):
        client = client_type.return_value
        client.request_json.return_value = {"organization": {"id": "org-1", "team_count": 8}, "replayed": False}
        result = execute_command(":org instantiate --confirm", state)

    assert result.handled is True
    call = client.request_json.call_args
    assert call.args == ("POST", "/api/organizations")
    assert call.kwargs["headers"]["If-Match"] == '"revision-1"'
    assert call.kwargs["headers"]["X-Plan-Digest"] == "digest-1"
    assert "organization_compile_plan" not in result.state.header_logo_game


def test_org_proposal_decision_reads_bound_revision_before_confirmed_write() -> None:
    with patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type:
        client = client_type.return_value
        client.request_json.side_effect = [
            {
                "proposals": [
                    {
                        "proposal_id": "proposal-1",
                        "revision": "3",
                        "digest": "digest-3",
                    }
                ]
            },
            {"proposal_id": "proposal-1", "status": "approved"},
        ]
        result = execute_command(
            ":org proposal org-1 proposal-1 approve --confirm",
            _state(),
        )

    assert result.handled is True
    write = client.request_json.call_args_list[1]
    assert write.args == ("POST", "/api/organizations/org-1/proposals/proposal-1/approve")
    assert write.kwargs["payload"] == {
        "expected_revision": 3,
        "expected_digest": "digest-3",
    }


def test_org_lifecycle_uses_lock_version_and_out_of_band_admin_grant() -> None:
    with (
        patch.dict(
            "os.environ",
            {"ANANTA_ORGANIZATION_ADMIN_GRANT": "grant-value"},
            clear=False,
        ),
        patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type,
    ):
        client = client_type.return_value
        client.request_json.side_effect = [
            {"id": "org-1", "lock_version": 7},
            {"organization_id": "org-1", "lifecycle": "paused", "lock_version": 8},
        ]
        result = execute_command(
            ":org lifecycle org-1 paused --strategy drain --confirm",
            _state(),
        )

    assert result.handled is True
    write = client.request_json.call_args_list[1]
    assert write.args == ("POST", "/api/organizations/org-1/lifecycle")
    assert write.kwargs["headers"]["If-Match"] == '"7"'
    assert write.kwargs["payload"]["active_work_strategy"] == "drain"


def test_org_show_mermaid_export_is_bounded_to_loaded_topology() -> None:
    topology = {
        "organization_id": "org-1",
        "definition_revision": "revision-1",
        "nodes": [
            {"id": "org-1", "kind": "organization", "label": "Medium", "depth": 0},
            {"id": "team-a", "kind": "team", "label": "Delivery A", "depth": 1},
        ],
        "edges": [
            {
                "id": "contains-1",
                "namespace": "hierarchy",
                "kind": "contains",
                "source_id": "org-1",
                "target_id": "team-a",
            }
        ],
    }
    with patch("client_surfaces.operator_tui.commands_organizations.OpsApiClient") as client_type:
        client_type.return_value.request_json.return_value = topology
        result = execute_command(":org show org-1 --mermaid", _state())

    assert result.message.startswith("flowchart TD")
    assert "contains" in result.message


def test_organization_renderer_shows_runtime_state_without_prompt_or_secret_fields() -> None:
    lines = _organization_content_lines(
        {
            "organization_mode": True,
            "view": "topology",
            "organization_id": "org-1",
            "topology": {
                "definition_revision": "revision-1",
                "nodes": [{"id": "team-a", "kind": "team", "label": "Delivery A", "depth": 1}],
                "edges": [],
                "runtime_overlay": {"nodes": [{"node_id": "team-a", "status": {"label": "active"}}]},
            },
            "private_prompt": "must not render",
        },
        width=120,
        compact=False,
    )

    rendered = "\n".join(lines)
    assert "Delivery A [active]" in rendered
    assert "must not render" not in rendered
