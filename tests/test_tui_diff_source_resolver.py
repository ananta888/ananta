from __future__ import annotations

import subprocess
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from client_surfaces.operator_tui.diff.diff_engine import DiffEngine
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "tests@example.local"], cwd=repo)
    _run(["git", "config", "user.name", "tests"], cwd=repo)
    (repo / "demo.txt").write_text("line1\nline2\n", encoding="utf-8")
    _run(["git", "add", "demo.txt"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_resolver_loads_current_working_tree_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "demo.txt").write_text("line1\nline2 changed\n", encoding="utf-8")
    resolver = DiffSourceResolver(repo_root=repo)
    result = resolver.resolve(
        {
            "source_ref_id": "current",
            "source_kind": "git_diff",
            "display_name": "Current",
            "locator": {"base_ref": "HEAD"},
        }
    )
    assert result["ok"] is True
    assert result["content_type"] == "patch"
    assert "demo.txt" in result["patch"]


def test_resolver_loads_file_vs_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "demo.txt").write_text("line1\nline2 changed\n", encoding="utf-8")
    resolver = DiffSourceResolver(repo_root=repo)
    result = resolver.resolve(
        {
            "source_ref_id": "file",
            "source_kind": "file_path",
            "display_name": "File vs HEAD",
            "locator": {"path": "demo.txt", "against": "HEAD"},
        }
    )
    assert result["ok"] is True
    assert result["content_type"] == "pair"
    assert "line2\n" in result["left_text"]
    assert "line2 changed\n" in result["right_text"]


def test_resolver_loads_git_ref_vs_git_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "demo.txt").write_text("line1\nline2 changed\n", encoding="utf-8")
    _run(["git", "add", "demo.txt"], cwd=repo)
    _run(["git", "commit", "-m", "change"], cwd=repo)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, text=True, capture_output=True).stdout.strip()
    prev = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=str(repo), check=True, text=True, capture_output=True).stdout.strip()
    resolver = DiffSourceResolver(repo_root=repo)
    result = resolver.resolve(
        {
            "source_ref_id": "pair",
            "source_kind": "git_ref",
            "display_name": "Ref pair",
            "locator": {"left_ref": prev, "right_ref": head, "path": "demo.txt"},
        }
    )
    assert result["ok"] is True
    assert result["left_ref"] == prev
    assert result["right_ref"] == head


def test_resolver_loads_goal_output_artifact_content(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    report_file = repo / "report.txt"
    report_file.write_text("artifact output text\n", encoding="utf-8")
    service = GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path / "store"))
    service.record_output_artifact(
        goal_id="goal-1",
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-1",
            "goal_id": "goal-1",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": [],
            "artifact_ref": f"file:{report_file}",
            "content_hash": "a" * 64,
            "status": "created",
            "provenance_summary": "output",
        },
    )
    resolver = DiffSourceResolver(repo_root=repo, goal_artifact_service=service)
    result = resolver.resolve(
        {
            "source_ref_id": "out",
            "source_kind": "goal_output_artifact",
            "display_name": "Output",
            "locator": {"output_artifact_id": "out-1"},
        },
        goal_id="goal-1",
    )
    assert result["ok"] is True
    assert result["content_type"] == "text"
    assert "artifact output text" in result["text"]
    assert result["output_artifact_id"] == "out-1"


def test_resolver_returns_reason_code_for_missing_source(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    resolver = DiffSourceResolver(repo_root=repo)
    result = resolver.resolve(
        {
            "source_ref_id": "missing",
            "source_kind": "goal_output_artifact",
            "display_name": "Missing",
            "locator": {"output_artifact_id": "does-not-exist"},
        },
        goal_id="goal-1",
    )
    assert result["ok"] is False
    assert result["reason_code"] in {"output_not_found", "artifact_content_not_found"}


def test_output_artifact_vs_current_file_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    report_file = repo / "report.txt"
    report_file.write_text("v1\n", encoding="utf-8")
    current_file = repo / "demo.txt"
    current_file.write_text("v2\n", encoding="utf-8")
    service = GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path / "store-2"))
    service.record_output_artifact(
        goal_id="goal-2",
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-2",
            "goal_id": "goal-2",
            "task_id": "task-2",
            "worker_id": "worker-2",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": [],
            "artifact_ref": f"file:{report_file}",
            "content_hash": "b" * 64,
            "status": "created",
            "provenance_summary": "output",
        },
    )
    resolver = DiffSourceResolver(repo_root=repo, goal_artifact_service=service)
    left = resolver.resolve(
        {
            "source_ref_id": "out",
            "source_kind": "goal_output_artifact",
            "display_name": "Output",
            "locator": {"output_artifact_id": "out-2"},
        },
        goal_id="goal-2",
    )
    right_text = current_file.read_text(encoding="utf-8")
    doc = DiffEngine().build_document(
        left={"content_type": "text", "text": left["text"], "path": "report.txt"},
        right={"content_type": "text", "text": right_text, "path": "demo.txt"},
        render_mode="unified",
    )
    assert doc["stats"]["files"] == 1
    assert doc["files"][0]["status"] == "modified"


def test_output_artifact_vs_output_artifact_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    out1 = repo / "out1.txt"
    out2 = repo / "out2.txt"
    out1.write_text("one\n", encoding="utf-8")
    out2.write_text("two\n", encoding="utf-8")
    service = GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path / "store-3"))
    for output_id, path, hash_char in [("out-a", out1, "c"), ("out-b", out2, "d")]:
        service.record_output_artifact(
            goal_id="goal-3",
            output_artifact={
                "schema": "goal_output_artifact.v1",
                "output_artifact_id": output_id,
                "goal_id": "goal-3",
                "task_id": "task-3",
                "worker_id": "worker-3",
                "artifact_type": "report",
                "created_at": "2026-05-26T00:00:00Z",
                "input_usage_refs": [],
                "artifact_ref": f"file:{path}",
                "content_hash": hash_char * 64,
                "status": "created",
                "provenance_summary": "output",
            },
        )
    resolver = DiffSourceResolver(repo_root=repo, goal_artifact_service=service)
    left = resolver.resolve(
        {"source_ref_id": "out-a", "source_kind": "goal_output_artifact", "display_name": "A", "locator": {"output_artifact_id": "out-a"}},
        goal_id="goal-3",
    )
    right = resolver.resolve(
        {"source_ref_id": "out-b", "source_kind": "goal_output_artifact", "display_name": "B", "locator": {"output_artifact_id": "out-b"}},
        goal_id="goal-3",
    )
    doc = DiffEngine().build_document(
        left={"content_type": "text", "text": left["text"], "path": "out-a.txt"},
        right={"content_type": "text", "text": right["text"], "path": "out-b.txt"},
        render_mode="side_by_side",
    )
    rows = doc["files"][0]["hunks"][0]["rows"]
    assert any(row["status"] == "removed" for row in rows)
    assert any(row["status"] == "added" for row in rows)
