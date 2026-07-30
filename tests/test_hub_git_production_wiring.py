from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_control_bootstrap_composes_persistent_git_connectors():
    source = (
        ROOT / "agent/bootstrap/source_control_api.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }

    assert "compose_persistent_hub_git_source_connectors" in calls
    assert "build_source_control_connector_extensions" in calls
    assert "HubGitAuthorizationProvisioningService" in calls
    assert (
        "create_source_control_git_authorizations_blueprint" in calls
    )


def test_bootstrap_does_not_accept_git_provider_material_from_configuration():
    source = (
        ROOT / "agent/bootstrap/source_control_api.py"
    ).read_text(encoding="utf-8")

    assert "GITHUB_TOKEN" not in source
    assert "GITHUB_CLONE_URL" not in source
    assert "GIT_REMOTE_URL" not in source
    assert "hub_git_authorization_provisioner" in source
    assert "hub_git_secret_resolver" in source
