from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SOURCE = (
    ROOT
    / "client_surfaces"
    / "eclipse_runtime"
    / "ananta_eclipse_plugin"
    / "src"
    / "main"
    / "java"
    / "io"
    / "ananta"
    / "eclipse"
    / "runtime"
    / "core"
    / "AnantaApiClient.java"
)


def _registered_paths(app) -> set[str]:
    return {str(rule.rule) for rule in app.url_map.iter_rules()}


def test_eclipse_java_client_uses_registered_generic_hub_routes(app) -> None:
    paths = _registered_paths(app)
    source = CLIENT_SOURCE.read_text(encoding="utf-8")

    required_routes = {
        "/health",
        "/v1/ananta/capabilities",
        "/goals",
        "/tasks",
        "/tasks/<tid>",
        "/tasks/<tid>/review",
        "/artifacts",
        "/artifacts/<artifact_id>",
        "/api/system/audit-logs",
    }
    assert required_routes.issubset(paths)

    removed_eclipse_only_routes = {
        '"/capabilities"',
        '"/approvals"',
        '"/audit"',
        '"/repairs"',
        '"/projects/new"',
        '"/projects/evolve"',
        '"/tasks/analyze"',
        '"/tasks/review"',
        '"/tasks/patch-plan"',
    }
    assert not any(route in source for route in removed_eclipse_only_routes)


def test_eclipse_goal_payload_matches_hub_goal_create_contract() -> None:
    source = CLIENT_SOURCE.read_text(encoding="utf-8")

    assert 'body.append("{\\"goal\\":\\"")' in source
    assert "goal_text" not in source
    assert 'appendOptionalString(body, "operation_preset", operationPreset)' in source
    assert 'appendOptionalString(body, "command_id", commandId)' in source
