from __future__ import annotations

import subprocess

import scripts.smoke_nvim_runtime as smoke_nvim_runtime


def test_smoke_script_checks_include_all_required_commands() -> None:
    checks = smoke_nvim_runtime._build_check_commands()  # noqa: SLF001

    for command in smoke_nvim_runtime.REQUIRED_COMMANDS:
        assert f"missing_command:{command}" in checks
    assert "require('ananta').analyze()" in checks
    assert "nvim-runtime-smoke-ok" in checks


def test_smoke_script_returns_skip_when_nvim_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr(smoke_nvim_runtime.shutil, "which", lambda _name: None)
    ok, output = smoke_nvim_runtime.run_smoke_once()

    assert ok is True
    assert output == "nvim-runtime-smoke-skipped:nvim-not-found"


def test_smoke_script_reports_failure_for_missing_runtime_command(monkeypatch) -> None:
    monkeypatch.setattr(smoke_nvim_runtime.shutil, "which", lambda _name: "/usr/bin/nvim")

    def fake_run(*args, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(args=["nvim"], returncode=2, stdout="", stderr="missing_command:AnantaReview")

    monkeypatch.setattr(smoke_nvim_runtime.subprocess, "run", fake_run)
    ok, output = smoke_nvim_runtime.run_smoke_once()

    assert ok is False
    assert "missing_command:AnantaReview" in output
