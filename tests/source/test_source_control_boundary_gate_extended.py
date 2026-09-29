from __future__ import annotations

from scripts.check_source_control_boundaries import (
    check_source_control_boundaries,
)


def _bootstrap_with_extension(tmp_path, *, register: bool = True) -> None:
    (tmp_path / "agent/bootstrap").mkdir(parents=True, exist_ok=True)
    registration = (
        "app.register_blueprint("
        "create_source_control_git_authorizations_blueprint(service))\n"
        if register
        else ""
    )
    body = registration or "return None\n"
    (
        tmp_path / "agent/bootstrap/source_control_api.py"
    ).write_text(
        "from agent.routes.source_control_git_authorizations import "
        "create_source_control_git_authorizations_blueprint\n"
        "SourceControlRuntimeObservability = object\n"
        "SourceControlRolloutPolicy = object\n"
        "create_source_control_operations_blueprint = object\n"
        "def register(app, service):\n"
        "    "
        + body,
        encoding="utf-8",
    )


def _extension_route(tmp_path, *, access_guard: bool = True) -> None:
    (tmp_path / "agent/routes").mkdir(parents=True, exist_ok=True)
    guard = "@_access_guard\n" if access_guard else ""
    (
        tmp_path
        / "agent/routes/source_control_git_authorizations.py"
    ).write_text(
        "from flask import Blueprint\n"
        "bp = Blueprint('git_auth', __name__, "
        "url_prefix='/api/source-control/v1')\n"
        "def create_source_control_git_authorizations_blueprint():\n"
        "    return bp\n"
        "@bp.get('/git-authorizations')\n"
        "@check_auth\n"
        f"{guard}"
        "def list_authorizations():\n"
        "    return {}\n",
        encoding="utf-8",
    )


def test_verified_v1_extension_is_accepted(tmp_path) -> None:
    _bootstrap_with_extension(tmp_path)
    _extension_route(tmp_path)

    violations = check_source_control_boundaries(tmp_path)

    assert not any(
        item.code
        == "source_control_v1_route_outside_canonical_bootstrap"
        for item in violations
    )


def test_v1_extension_without_access_guard_is_rejected(tmp_path) -> None:
    _bootstrap_with_extension(tmp_path)
    _extension_route(tmp_path, access_guard=False)

    violations = check_source_control_boundaries(tmp_path)

    assert any(
        item.code
        == "source_control_v1_route_outside_canonical_bootstrap"
        for item in violations
    )


def test_v1_extension_without_bootstrap_registration_is_rejected(
    tmp_path,
) -> None:
    _bootstrap_with_extension(tmp_path, register=False)
    _extension_route(tmp_path)

    violations = check_source_control_boundaries(tmp_path)

    assert any(
        item.code
        == "source_control_v1_route_outside_canonical_bootstrap"
        for item in violations
    )


def test_indirect_hub_to_worker_import_is_reported(tmp_path) -> None:
    (tmp_path / "agent/bootstrap").mkdir(parents=True)
    (tmp_path / "agent/services").mkdir(parents=True)
    (tmp_path / "agent/routes").mkdir(parents=True)
    (tmp_path / "agent/bootstrap/source_control_api.py").write_text(
        "from agent.services.source_control_bridge import bridge\n"
        "SourceControlRuntimeObservability = object\n"
        "SourceControlRolloutPolicy = object\n"
        "create_source_control_operations_blueprint = object\n",
        encoding="utf-8",
    )
    (tmp_path / "agent/services/source_control_bridge.py").write_text(
        "from agent.services.deep_bridge import bridge\n",
        encoding="utf-8",
    )
    (tmp_path / "agent/services/deep_bridge.py").write_text(
        "from worker.runtime import execute\n",
        encoding="utf-8",
    )

    violations = check_source_control_boundaries(tmp_path)

    assert any(
        item.code == "hub_indirectly_imports_worker_implementation"
        and "deep_bridge" in item.detail
        for item in violations
    )


def test_v1_route_without_auth_is_reported(tmp_path) -> None:
    (tmp_path / "agent/routes").mkdir(parents=True)
    (tmp_path / "agent/routes/custom.py").write_text(
        "from flask import Blueprint\n"
        "bp = Blueprint('x', __name__, url_prefix='/api/source-control/v1')\n"
        "@bp.get('/unsafe')\n"
        "def unsafe():\n"
        "    return {}\n",
        encoding="utf-8",
    )

    violations = check_source_control_boundaries(tmp_path)

    assert any(
        item.code == "public_route_missing_auth"
        and item.detail == "unsafe"
        for item in violations
    )
