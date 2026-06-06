from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from agent.routes.snakes import snakes_bp
from agent.services.worker_context_handoff_diagnostics_service import (
    WorkerContextHandoffDiagnosticsService,
)
from agent.services.worker_context_request_service import WorkerContextRequestService


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


def test_worker_context_endpoint_builds_v3_payload_with_diagnostics(tmp_path: Path) -> None:
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

    response = app.test_client().post(
        "/worker-context",
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
