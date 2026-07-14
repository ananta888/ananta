from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from agent.routes.snakes import snakes_bp
from agent.services.worker_context_handoff_diagnostics_service import (
    WorkerContextHandoffDiagnosticsService,
)
from agent.services.worker_context_request_service import WorkerContextRequestService


def _user_auth_header() -> dict[str, str]:
    from agent.config import settings
    from agent.services.user_session_tokens import issue_user_access_token

    token = issue_user_access_token(username=settings.initial_admin_user, role="admin")
    return {"Authorization": f"Bearer {token}"}


def test_handoff_diagnostics_reports_missing_required_reads() -> None:
    handoff = {
        "schema": "worker_context_handoff.v3",
        "candidate_files": [
            {
                "path": "src/a.py",
                "requires_read": True,
                "source_output_kinds": ["context"],
            }
        ],
        "context_files": [],
        "required_reads": ["src/a.py"],
        "policy_version": "v3.0",
    }

    diagnostics = WorkerContextHandoffDiagnosticsService().summarize(handoff)

    assert diagnostics["candidate_file_count"] == 1
    assert diagnostics["context_file_count"] == 0
    assert diagnostics["missing_required_reads"] == ["src/a.py"]
    assert diagnostics["source_output_kinds"] == ["context"]


def test_worker_context_request_service_fulfills_read_file(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("value = 1\n", encoding="utf-8")

    result = WorkerContextRequestService().fulfill(
        [{"action": "read_file", "path": "src/a.py"}],
        workspace_root=tmp_path,
    )

    assert result["schema"] == "worker_context_request_result.v1"
    assert result["errors"] == []
    assert result["context_files"][0]["path"] == "src/a.py"
    assert "value = 1" in result["context_files"][0]["content"]


def test_worker_context_request_service_blocks_unsafe_request(tmp_path: Path) -> None:
    result = WorkerContextRequestService().fulfill(
        [{"action": "execute_command", "path": "src/a.py"}, {"action": "read_file", "path": "../x.py"}],
        workspace_root=tmp_path,
    )

    assert result["context_files"] == []
    assert result["errors"][0]["error"] == "unsupported_action:execute_command"
    assert "traversal" in result["errors"][1]["error"]


def test_worker_context_endpoint_builds_v3_payload_with_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.config import settings

    workspace = tmp_path / "workspace"
    output = tmp_path / "codecompass"
    workspace.mkdir()
    output.mkdir()
    source = workspace / "src"
    source.mkdir()
    (source / "foo.py").write_text("class FooService:\n    pass\n", encoding="utf-8")
    (output / "context.jsonl").write_text(
        json.dumps({"id": "ctx-1", "path": "src/foo.py", "content": "FooService handles foo"}) + "\n",
        encoding="utf-8",
    )

    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(snakes_bp)
    monkeypatch.setattr(settings, "hub_workspace_root", str(tmp_path))

    response = app.test_client().post(
        "/worker-context",
        headers=_user_auth_header(),
        json={
            "question": "Wo ist FooService?",
            "output_dir": str(output),
            "workspace_root": str(workspace),
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "worker_context_handoff.v3"
    assert payload["candidate_files"][0]["path"] == "src/foo.py"
    assert payload["context_files"][0]["path"] == "src/foo.py"
    assert payload["diagnostics"]["context_file_count"] == 1
    assert payload["diagnostics"]["missing_required_reads"] == []


def test_worker_context_rejects_unauthenticated_docker_bridge_caller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.config import settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(settings, "hub_workspace_root", str(tmp_path))
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(snakes_bp)

    response = app.test_client().post(
        "/worker-context",
        environ_base={"REMOTE_ADDR": "172.18.0.5"},
        json={
            "question": "Where is Foo?",
            "output_dir": str(workspace),
        },
    )

    assert response.status_code == 401


def test_worker_context_rejects_output_outside_hub_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.config import settings

    hub_root = tmp_path / "hub-owned"
    outside = tmp_path / "caller-controlled"
    hub_root.mkdir()
    outside.mkdir()
    monkeypatch.setattr(settings, "hub_workspace_root", str(hub_root))
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(snakes_bp)

    response = app.test_client().post(
        "/worker-context",
        headers=_user_auth_header(),
        json={
            "question": "Where is Foo?",
            "output_dir": str(outside),
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "worker_context_path_rejected",
        "field": "output_dir",
        "reason_code": "output_dir_outside_hub_workspace",
    }


def test_worker_context_rejects_workspace_symlink_escape(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.config import settings

    hub_root = tmp_path / "hub-owned"
    output = hub_root / "output"
    outside = tmp_path / "outside"
    hub_root.mkdir()
    output.mkdir()
    outside.mkdir()
    escape = hub_root / "escaped-workspace"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    monkeypatch.setattr(settings, "hub_workspace_root", str(hub_root))
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(snakes_bp)

    response = app.test_client().post(
        "/worker-context",
        headers=_user_auth_header(),
        json={
            "question": "Where is Foo?",
            "output_dir": str(output),
            "workspace_root": str(escape),
        },
    )

    assert response.status_code == 400
    assert response.get_json()["reason_code"] == "workspace_root_outside_hub_workspace"


def test_worker_context_accepts_strict_service_bearer() -> None:
    service_token = "worker-context-service-token-with-at-least-32-bytes"
    app = Flask(__name__)
    app.testing = True
    app.config["AGENT_TOKEN"] = service_token
    app.register_blueprint(snakes_bp)

    response = app.test_client().post(
        "/worker-context",
        headers={"Authorization": f"Bearer {service_token}"},
        json={},
    )

    # The request crossed the strict service-auth boundary and reached input
    # validation; an unauthenticated request would have returned 401.
    assert response.status_code == 400
    assert response.get_json()["error"] == "question required"
