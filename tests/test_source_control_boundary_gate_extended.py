from __future__ import annotations

from scripts.check_source_control_boundaries import (
    check_source_control_boundaries,
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
