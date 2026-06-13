import json

from agent.services.codecompass_context_service import (
    SCHEMA_CONTEXT_PACKAGE,
    SCHEMA_FILE_CONTEXT_RESULT,
    CodeCompassContextService,
    CodeCompassContextToolConfig,
)
from agent.services.tools import execute_ananta_tool


class _FakeRag:
    def __init__(self, hits):
        self._hits = list(hits)

    def retrieve(self, *, profile, query, limit):
        del profile, query
        return self._hits[:limit]


def _patch_rag(monkeypatch, hits):
    monkeypatch.setattr(
        "agent.services.rag_helper_index_service.get_rag_helper_index_service",
        lambda: _FakeRag(hits),
    )


def test_resolve_context_prioritizes_working_files_and_provenance(monkeypatch, tmp_path):
    _patch_rag(
        monkeypatch,
        [
            {
                "path": "src/other.py",
                "content": "def other(): pass",
                "metadata": {"symbol": "other", "start_line": 1, "end_line": 1},
                "score": 0.8,
            }
        ],
    )
    svc = CodeCompassContextService()

    package = svc.resolve_context(
        query="explain service",
        mode="implementation",
        working_files=["src/service.py"],
        max_files=5,
        workspace_dir=str(tmp_path),
    )

    assert package["schema"] == SCHEMA_CONTEXT_PACKAGE
    assert package["candidate_files"][0]["path"] == "src/service.py"
    assert package["candidate_files"][0]["reason"] == "explicit_working_file"
    assert package["candidate_files"][0]["provenance"]["manifest_hash"]
    assert package["why_this_context"]


def test_get_file_context_blocks_traversal_and_redacts_secret(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("token = 'abc12345678901234567890'\nprint(token)\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("password=outside", encoding="utf-8")

    svc = CodeCompassContextService(
        config=CodeCompassContextToolConfig(require_reason_for_file_context=True, max_total_bytes=10_000)
    )
    result = svc.get_file_context(
        paths=["app.py", "../secret.txt"],
        line_ranges=[{"path": "app.py", "line_start": 1, "line_end": 1}],
        reason="test",
        workspace_dir=str(workspace),
    )

    assert result["schema"] == SCHEMA_FILE_CONTEXT_RESULT
    assert result["context_files"][0]["path"] == "app.py"
    assert "[REDACTED]" in result["context_files"][0]["content"]
    assert result["context_files"][0]["line_ranges"][0]["line_start"] == 1
    assert result["denied_items"][0]["reason_code"] == "path_outside_workspace"


def test_get_file_context_requires_reason(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('x')", encoding="utf-8")
    svc = CodeCompassContextService()

    result = svc.get_file_context(paths=["app.py"], workspace_dir=str(workspace))

    assert result["status"] == "error"
    assert result["error"] == "reason_required"


def test_execute_resolve_context_tool_returns_context_package(monkeypatch, tmp_path):
    _patch_rag(
        monkeypatch,
        [
            {
                "path": "src/service.py",
                "content": "def service(): pass",
                "metadata": {"symbol": "service", "start_line": 1, "end_line": 1},
                "score": 0.9,
            }
        ],
    )

    result = execute_ananta_tool(
        tool_name="codecompass.resolve_context",
        arguments={"query": "service", "max_files": 3},
        workspace_dir=str(tmp_path),
        tool_call_id="tool:1",
    )

    assert result["status"] == "ok"
    package = result["data"]["context_package"]
    assert package["schema"] == SCHEMA_CONTEXT_PACKAGE
    assert package["candidate_files"][0]["path"] == "src/service.py"


def test_schema_file_contains_required_context_package_fields():
    schema = json.loads(open("docs/schemas/codecompass_context_package.v1.schema.json", encoding="utf-8").read())

    assert schema["properties"]["schema"]["const"] == SCHEMA_CONTEXT_PACKAGE
    for key in ["candidate_files", "context_files", "denied_items", "warnings"]:
        assert key in schema["required"]
