from __future__ import annotations

import ast
from pathlib import Path

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile

ROOT = Path(__file__).resolve().parents[1]
CLIENT_THINNESS_DOC = ROOT / "docs" / "architecture" / "client_thinness_rules.md"

FORBIDDEN_IMPORTS = {"subprocess", "pty", "pexpect"}
FORBIDDEN_AGENT_PREFIXES = {
    "agent.routes",
    "agent.services.task_scoped_execution_service",
    "agent.services.domain_action_router",
    "agent.shell",
}


def _client_python_files() -> list[Path]:
    return sorted(
        path
        for path in (ROOT / "client_surfaces").rglob("*.py")
        if "__pycache__" not in path.parts and "test" not in path.name.lower()
    )


def test_client_thinness_rules_document_covers_required_surfaces_and_boundaries() -> None:
    content = CLIENT_THINNESS_DOC.read_text(encoding="utf-8").lower()
    assert "cli" in content
    assert "tui" in content
    assert "neovim" in content
    assert "eclipse" in content
    assert "blender" in content
    assert "vs code" in content
    assert "may not orchestrate" in content
    assert "no direct tool execution" in content


def test_client_surfaces_do_not_import_local_execution_or_orchestration_primitives() -> None:
    forbidden_hits: list[str] = []
    for path in _client_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = str(alias.name)
                    if module.split(".", 1)[0] in FORBIDDEN_IMPORTS:
                        forbidden_hits.append(f"{rel}:import:{module}")
                    if any(module.startswith(prefix) for prefix in FORBIDDEN_AGENT_PREFIXES):
                        forbidden_hits.append(f"{rel}:import:{module}")
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.split(".", 1)[0] in FORBIDDEN_IMPORTS:
                    forbidden_hits.append(f"{rel}:from:{module}")
                if any(module.startswith(prefix) for prefix in FORBIDDEN_AGENT_PREFIXES):
                    forbidden_hits.append(f"{rel}:from:{module}")
    assert forbidden_hits == []


def test_clients_surface_denied_approval_required_and_degraded_states_without_local_actions() -> None:
    calls: list[tuple[str, str]] = []

    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        calls.append((method, path))
        routes = {
            ("POST", "/goals"): (403, '{"error":"policy_denied"}'),
            ("POST", "/tasks/task-1/review"): (422, '{"error":"approval_required"}'),
            ("GET", "/tasks"): (503, '{"error":"backend_unavailable"}'),
        }
        return routes[(method, path)]

    client = AnantaApiClient(
        build_client_profile({"profile_id": "thinness", "base_url": "http://localhost:8080"}),
        transport=transport,
    )

    denied = client.submit_goal("goal text", {"schema": "client_bounded_context_payload_v1", "selection_text": "x"})
    approval_required = client.review_task_proposal("task-1", action="approve")
    degraded = client.list_tasks()

    assert denied.ok is False and denied.state == "policy_denied"
    assert approval_required.ok is False and approval_required.state == "capability_missing"
    assert degraded.ok is False and degraded.state == "backend_unreachable"
    assert calls == [
        ("POST", "/goals"),
        ("POST", "/tasks/task-1/review"),
        ("GET", "/tasks"),
    ]

