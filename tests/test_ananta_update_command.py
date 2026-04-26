from __future__ import annotations

import subprocess
from pathlib import Path

from agent.cli import update as update_cli


def _completed(*, command: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=command, returncode=returncode, stdout=stdout, stderr=stderr)


def _write_minimal_repo(repo_dir: Path) -> None:
    (repo_dir / ".git").mkdir()
    (repo_dir / "pyproject.toml").write_text('[project]\nname = "ananta"\n', encoding="utf-8")


def test_ananta_update_clean_flow_runs_git_deps_and_smoke(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_dir = tmp_path / "ananta"
    repo_dir.mkdir()
    _write_minimal_repo(repo_dir)
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")

    calls: list[list[str]] = []
    head_calls = {"count": 0}

    def fake_run(command, cwd=None, check=False, text=True, capture_output=True):  # noqa: ANN001, ARG001
        cmd = [str(part) for part in command]
        calls.append(cmd)
        if cmd == ["git", "status", "--porcelain"]:
            return _completed(command=cmd, stdout="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _completed(command=cmd, stdout="main\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            head_calls["count"] += 1
            return _completed(command=cmd, stdout="oldsha\n" if head_calls["count"] == 1 else "newsha\n")
        if cmd == ["git", "fetch", "--tags", "--prune", "origin"]:
            return _completed(command=cmd)
        if cmd == ["git", "pull", "--ff-only", "origin", "main"]:
            return _completed(command=cmd)
        if cmd[0:4] == [str(python_exe), "-m", "pip", "install"]:
            return _completed(command=cmd)
        if cmd == [str(python_exe), "-m", "agent.cli.main", "--help"]:
            return _completed(command=cmd)
        return _completed(command=cmd, returncode=1, stderr="unexpected command")

    monkeypatch.setattr(update_cli.subprocess, "run", fake_run)

    rc = update_cli.main(["--repo-dir", str(repo_dir), "--python", str(python_exe)])

    out = capsys.readouterr().out
    assert rc == 0
    assert ["git", "pull", "--ff-only", "origin", "main"] in calls
    assert [str(python_exe), "-m", "agent.cli.main", "--help"] in calls
    assert "Rollback command:" in out
    assert "oldsha" in out


def test_ananta_update_refuses_dirty_worktree_without_allow_flag(monkeypatch, tmp_path: Path) -> None:
    repo_dir = tmp_path / "ananta"
    repo_dir.mkdir()
    _write_minimal_repo(repo_dir)

    calls: list[list[str]] = []

    def fake_run(command, cwd=None, check=False, text=True, capture_output=True):  # noqa: ANN001, ARG001
        cmd = [str(part) for part in command]
        calls.append(cmd)
        if cmd == ["git", "status", "--porcelain"]:
            return _completed(command=cmd, stdout=" M todo.json\n")
        return _completed(command=cmd)

    monkeypatch.setattr(update_cli.subprocess, "run", fake_run)

    rc = update_cli.main(["--repo-dir", str(repo_dir)])

    assert rc == 1
    assert ["git", "fetch", "--tags", "--prune", "origin"] not in calls


def test_ananta_update_reports_smoke_failure(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_dir = tmp_path / "ananta"
    repo_dir.mkdir()
    _write_minimal_repo(repo_dir)
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")

    def fake_run(command, cwd=None, check=False, text=True, capture_output=True):  # noqa: ANN001, ARG001
        cmd = [str(part) for part in command]
        if cmd == ["git", "status", "--porcelain"]:
            return _completed(command=cmd, stdout="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _completed(command=cmd, stdout="main\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed(command=cmd, stdout="same\n")
        if cmd == ["git", "fetch", "--tags", "--prune", "origin"]:
            return _completed(command=cmd)
        if cmd == ["git", "pull", "--ff-only", "origin", "main"]:
            return _completed(command=cmd)
        if cmd == [str(python_exe), "-m", "agent.cli.main", "--help"]:
            return _completed(command=cmd, returncode=2, stderr="smoke failed")
        return _completed(command=cmd)

    monkeypatch.setattr(update_cli.subprocess, "run", fake_run)

    rc = update_cli.main(["--repo-dir", str(repo_dir), "--python", str(python_exe), "--skip-deps"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "command failed" in out
    assert "agent.cli.main" in out
    assert "smoke failed" in out


def test_ananta_update_supports_explicit_rollback_ref(monkeypatch, tmp_path: Path) -> None:
    repo_dir = tmp_path / "ananta"
    repo_dir.mkdir()
    _write_minimal_repo(repo_dir)
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, cwd=None, check=False, text=True, capture_output=True):  # noqa: ANN001, ARG001
        cmd = [str(part) for part in command]
        calls.append(cmd)
        if cmd == ["git", "status", "--porcelain"]:
            return _completed(command=cmd, stdout="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _completed(command=cmd, stdout="main\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed(command=cmd, stdout="rollbacksha\n")
        return _completed(command=cmd)

    monkeypatch.setattr(update_cli.subprocess, "run", fake_run)

    rc = update_cli.main(
        [
            "--repo-dir",
            str(repo_dir),
            "--python",
            str(python_exe),
            "--rollback-to",
            "abc123",
            "--skip-deps",
            "--skip-smoke",
        ]
    )

    assert rc == 0
    assert ["git", "checkout", "abc123"] in calls
    assert ["git", "fetch", "--tags", "--prune", "origin"] not in calls
