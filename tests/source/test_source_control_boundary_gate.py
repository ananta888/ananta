from __future__ import annotations

from pathlib import Path

from scripts.check_source_control_boundaries import (
    check_source_control_boundaries,
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _route_files(root: Path, *, authenticated: bool = True) -> None:
    decorator = "@check_auth\n" if authenticated else ""
    route_source = (
        "from flask import Blueprint\n"
        "bp = Blueprint('example', __name__)\n"
        "@bp.route('/example')\n"
        f"{decorator}"
        "def example():\n"
        "    return {}\n"
    )
    for relative in (
        "agent/routes/sources.py",
        "agent/routes/knowledge.py",
        "agent/routes/codecompass_graph.py",
        "agent/routes/codecompass_domain_scope.py",
        "agent/routes/codecompass_reload.py",
        "agent/routes/context_policy.py",
    ):
        _write(root, relative, route_source)


def test_boundary_gate_detects_worker_import_and_missing_auth(
    tmp_path: Path,
) -> None:
    _route_files(tmp_path, authenticated=False)
    _write(
        tmp_path,
        "agent/services/example.py",
        "from worker.core.context_access_policy import Decision\n",
    )

    violations = check_source_control_boundaries(tmp_path)
    codes = {item.code for item in violations}

    assert "hub_imports_worker_implementation" in codes
    assert "public_route_missing_auth" in codes


def test_boundary_gate_detects_connector_orchestration(
    tmp_path: Path,
) -> None:
    _route_files(tmp_path)
    _write(
        tmp_path,
        "agent/sources/bad_connector.py",
        "from agent.services.task_execution_service import enqueue\n",
    )

    violations = check_source_control_boundaries(tmp_path)

    assert any(
        item.code == "connector_orchestrates_or_depends_on_worker"
        for item in violations
    )


def test_boundary_gate_accepts_neutral_contracts_and_authenticated_routes(
    tmp_path: Path,
) -> None:
    _route_files(tmp_path)
    _write(
        tmp_path,
        "agent/services/example.py",
        "from ananta_contracts.context_access_policy import Decision\n",
    )
    _write(
        tmp_path,
        "agent/sources/example.py",
        "from ananta_contracts.source_control import SourceRevision\n",
    )

    assert check_source_control_boundaries(tmp_path) == []
