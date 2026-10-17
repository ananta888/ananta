"""A task that declares a shared directory gets its own workspace copy.

The declared ``output_dir`` of an ingest task pointed at a shared checkout without a
materialization manifest: one task's leftover test file and ``.pytest_cache`` became the next
task's baseline, and no per-task scope could be decided. Such a workspace is copied into
``<workspace_root>/self-dev-<task_id>`` once and gets its own manifest; everything that *is*
declared sharing (an existing manifest, a not-yet-existing directory, ``goal_worker`` reuse) is
left exactly as it was.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from agent.services.worker_workspace_service import WorkerWorkspaceService


def _app(tmp_path: Path, **config: object) -> Flask:
    app = Flask("workspace-isolation-test")
    app.config["AGENT_NAME"] = "worker"
    app.config["AGENT_CONFIG"] = {
        "worker_runtime": {"workspace_root": str(tmp_path / "root")},
        "output_dir_policy": {"unsafe_shared": False},
        "ananta_worker_workspace_mutation": {"allowed_new_file_globs": ["tests/*", "agent/*"]},
        **config,
    }
    return app


def _shared_checkout(tmp_path: Path) -> Path:
    shared = tmp_path / "root" / "shared"
    (shared / "tests").mkdir(parents=True)
    (shared / "tests" / "test_growth.py").write_text("def test_growth():\n    pass\n", encoding="utf-8")
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "note.md").write_text("shared note\n", encoding="utf-8")
    return shared


def _task(task_id: str, output_dir: Path, **workspace_cfg: object) -> dict:
    return {
        "id": task_id,
        "worker_execution_context": {"workspace": {"output_dir": str(output_dir), **workspace_cfg}},
    }


def test_a_shared_checkout_is_copied_into_a_task_owned_workspace_with_a_manifest(tmp_path):
    shared = _shared_checkout(tmp_path)
    task = _task("abc-123", shared, scope_key="shared")

    with _app(tmp_path).app_context():
        service = WorkerWorkspaceService()
        resolved = service.resolve_workspace_dir_for_read(task=task)
        assert resolved == tmp_path / "root" / "self-dev-abc-123"
        context = service.resolve_workspace_context(task=task)

    target = tmp_path / "root" / "self-dev-abc-123"
    assert context.workspace_dir == target
    assert (target / "tests" / "test_growth.py").read_text(encoding="utf-8").startswith("def test_growth")

    manifest = json.loads((target / ".ananta" / "materialization-manifest.json").read_text(encoding="utf-8"))
    declared = [row["workspace_path"] for row in manifest]
    assert "tests/test_growth.py" in declared  # inherited in-glob baseline is declared
    assert "docs/note.md" not in declared  # outside the declared globs
    for row in manifest:
        assert row["allowed_operations"] == ["read", "write", "patch", "replace"]

    # the shared checkout is read, never written to
    assert not (shared / ".ananta").exists()


def test_a_workspace_resolves_to_the_same_dir_on_every_call(tmp_path):
    shared = _shared_checkout(tmp_path)
    task = _task("abc-123", shared, scope_key="shared")

    with _app(tmp_path).app_context():
        service = WorkerWorkspaceService()
        first = service.resolve_workspace_dir_for_read(task=task)
        second = service.resolve_workspace_dir_for_read(task=task)
        service.resolve_workspace_context(task=task)
        third = service.resolve_workspace_dir_for_read(task=task)

    assert first == second == third == tmp_path / "root" / "self-dev-abc-123"
    # the copy is not repeated
    service = WorkerWorkspaceService()
    with _app(tmp_path).app_context():
        service.resolve_workspace_context(task=task)
    assert (tmp_path / "root" / "self-dev-abc-123" / "tests" / "test_growth.py").exists()


def test_declared_sharing_a_manifest_a_missing_directory_and_goal_worker_stay_untouched(tmp_path):
    with_manifest = tmp_path / "root" / "with-manifest"
    (with_manifest / ".ananta").mkdir(parents=True)
    (with_manifest / ".ananta" / "materialization-manifest.json").write_text("[]", encoding="utf-8")
    shared = _shared_checkout(tmp_path)
    missing = tmp_path / "root" / "not-created-yet"

    app = _app(tmp_path)
    with app.app_context():
        service = WorkerWorkspaceService()

        # a directory that already declares its scope is used as it is
        assert service.resolve_workspace_dir_for_read(task=_task("a", with_manifest)) == with_manifest
        # a declared directory that does not exist yet is a normal workspace, not a copy source
        assert service.resolve_workspace_dir_for_read(task=_task("b", missing)) != tmp_path / "root" / "self-dev-b"
        # a goal-scoped task shares its directory with its siblings on purpose
        goal_task = _task("c", shared, workspace_reuse_mode="goal_worker")
        goal_task["goal_id"] = "g-1"
        assert service.resolve_workspace_dir_for_read(task=goal_task) == shared
        # the same task without a goal is isolated -- the reuse mode alone is a global default
        assert service.resolve_workspace_dir_for_read(task=_task("e", shared, workspace_reuse_mode="goal_worker")) == (
            tmp_path / "root" / "self-dev-e"
        )
        # without a declared output_dir nothing changes either
        plain = {"id": "d", "worker_execution_context": {}}
        resolved_plain = service.resolve_workspace_dir_for_read(task=plain)
    assert str(resolved_plain).startswith(str(tmp_path / "root"))
