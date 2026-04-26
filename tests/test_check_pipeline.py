from __future__ import annotations

import pytest

import scripts.check_pipeline as check_pipeline


def test_check_pipeline_deep_ignores_fixture_repos(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], cwd=None):  # noqa: ANN001
        commands.append(list(command))
        return True

    monkeypatch.setattr(check_pipeline, "run_command", fake_run_command)
    monkeypatch.setattr(
        check_pipeline.sys, "argv", ["check_pipeline.py", "--mode", "deep", "--skip-style", "--skip-types"]
    )

    with pytest.raises(SystemExit) as exc:
        check_pipeline.main()

    assert exc.value.code == 0
    deep_commands = [cmd for cmd in commands if cmd[0:4] == [check_pipeline.sys.executable, "-m", "pytest", "tests"]]
    assert len(deep_commands) == 1
    assert "--ignore-glob=tests/**/fixtures/**" in deep_commands[0]


def test_check_pipeline_deep_fails_when_deep_pytest_fails(monkeypatch) -> None:
    def fake_run_command(command: list[str], cwd=None):  # noqa: ANN001
        if command[0:4] == [check_pipeline.sys.executable, "-m", "pytest", "tests"]:
            return False
        return True

    monkeypatch.setattr(check_pipeline, "run_command", fake_run_command)
    monkeypatch.setattr(
        check_pipeline.sys, "argv", ["check_pipeline.py", "--mode", "deep", "--skip-style", "--skip-types"]
    )

    with pytest.raises(SystemExit) as exc:
        check_pipeline.main()

    assert exc.value.code == 1
