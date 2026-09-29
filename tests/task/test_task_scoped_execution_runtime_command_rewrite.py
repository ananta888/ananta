from pathlib import Path

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


def test_rewrite_runtime_command_prefers_workspace_uvicorn_binary(tmp_path: Path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "uvicorn").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    command, meta = TaskScopedExecutionService._rewrite_runtime_command_for_workspace_tools(
        command="uvicorn src.main:app --reload",
        workspace_dir=str(tmp_path),
    )

    assert command is not None
    assert str(venv_bin / "uvicorn") in command
    assert isinstance(meta, dict)
    assert meta.get("strategy") == "workspace_venv_uvicorn_binary"


def test_rewrite_runtime_command_falls_back_to_activate_prefix(tmp_path: Path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "activate").write_text("# activate\n", encoding="utf-8")

    command, meta = TaskScopedExecutionService._rewrite_runtime_command_for_workspace_tools(
        command="uvicorn src.main:app --reload",
        workspace_dir=str(tmp_path),
    )

    assert command is not None
    assert command.startswith("source .venv/bin/activate && ")
    assert isinstance(meta, dict)
    assert meta.get("strategy") == "workspace_venv_activate_prefix"
