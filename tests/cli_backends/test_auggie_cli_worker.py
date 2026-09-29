import pytest
from unittest.mock import patch, MagicMock
from agent.services.augment.auggie_cli_worker import (
    AuggieCliWorker, WorkerMode, WorkerConfigError, WorkerSecurityError,
    ChangeProposal, TaskWorkspace, TestRunnerIntegration, ENV_ALLOWLIST
)
from agent.services.augment.augment_config import AugmentConfig, AugmentCliConfig, AugmentSecurityConfig

def _cfg(enabled=True, allow_write=False, workspace_mode="task_scoped_copy"):
    cfg = AugmentConfig()
    cfg.auggie_cli.enabled = enabled
    cfg.auggie_cli.allow_write = allow_write
    cfg.auggie_cli.timeout_seconds = 5
    cfg.auggie_cli.max_output_bytes = 1048576
    cfg.security.workspace_mode = workspace_mode
    cfg.security.denied_paths = [".env", ".git"]
    return cfg

def test_not_enabled_raises_on_run():
    worker = AuggieCliWorker(_cfg(enabled=False))
    with pytest.raises(WorkerConfigError):
        worker.run_read_only("task")

def test_write_proposal_requires_allow_write():
    worker = AuggieCliWorker(_cfg(enabled=True, allow_write=False))
    with pytest.raises(WorkerSecurityError):
        worker.run_write_proposal("task")

def test_write_proposal_requires_task_scoped_copy():
    worker = AuggieCliWorker(_cfg(enabled=True, allow_write=True, workspace_mode="direct"))
    with pytest.raises(WorkerSecurityError):
        worker.run_write_proposal("task")

def test_prompt_has_allowed_paths():
    worker = AuggieCliWorker(_cfg())
    prompt = worker.build_prompt("task", allowed_paths=["src/"], denied_paths=[], mode=WorkerMode.READ_ONLY)
    assert "src/" in prompt

def test_prompt_has_denied_paths():
    worker = AuggieCliWorker(_cfg())
    prompt = worker.build_prompt("task", allowed_paths=[], denied_paths=[".env", "secrets/"], mode=WorkerMode.READ_ONLY)
    assert ".env" in prompt

def test_prompt_forbids_secrets_output():
    worker = AuggieCliWorker(_cfg())
    prompt = worker.build_prompt("task", allowed_paths=[], denied_paths=[], mode=WorkerMode.READ_ONLY)
    assert "secret" in prompt.lower() or "credential" in prompt.lower()

def test_prompt_read_only_note():
    worker = AuggieCliWorker(_cfg())
    prompt = worker.build_prompt("task", allowed_paths=[], denied_paths=[], mode=WorkerMode.READ_ONLY)
    assert "READ-ONLY" in prompt or "read_only" in prompt

def test_auggie_not_found_returns_exit_127():
    with patch("shutil.which", return_value=None):
        worker = AuggieCliWorker(_cfg())
        result = worker._execute("run1", "prompt", WorkerMode.READ_ONLY, None)
    assert result.exit_code == 127
    assert "not found" in result.stderr.lower() or "not in PATH" in result.warnings[0]

def test_env_allowlist_no_secrets():
    worker = AuggieCliWorker(_cfg())
    import os
    os.environ["FAKE_TOKEN"] = "supersecret"
    env = worker._build_env("/tmp/ws")
    assert "FAKE_TOKEN" not in env
    os.environ.pop("FAKE_TOKEN", None)

def test_redact_output_removes_tokens():
    worker = AuggieCliWorker(_cfg())
    text = "token=mytoken123 key=apikey456"
    redacted = worker._redact_output(text)
    assert "mytoken123" not in redacted
    assert "[REDACTED]" in redacted

def test_read_only_run_returns_result_when_no_binary():
    with patch("shutil.which", return_value=None):
        worker = AuggieCliWorker(_cfg())
        result = worker.run_read_only("analyze the code")
    assert result.mode == WorkerMode.READ_ONLY
    assert result.exit_code == 127

def test_write_proposal_with_no_binary():
    cfg = _cfg(enabled=True, allow_write=True)
    with patch("shutil.which", return_value=None):
        worker = AuggieCliWorker(cfg)
        result, proposal = worker.run_write_proposal("add feature")
    assert result.exit_code == 127
    assert proposal.approval_required is True

def test_change_proposal_has_run_id():
    cfg = _cfg(enabled=True, allow_write=True)
    with patch("shutil.which", return_value=None):
        worker = AuggieCliWorker(cfg)
        result, proposal = worker.run_write_proposal("task")
    assert proposal.run_id == result.run_id

def test_test_runner_uses_project_commands():
    tr = TestRunnerIntegration(project_test_commands=["python -m pytest tests/"])
    cmds = tr.get_commands()
    assert "python -m pytest tests/" in cmds

def test_test_runner_filters_auggie_suggestions():
    tr = TestRunnerIntegration(project_test_commands=["python -m pytest tests/"])
    safe = tr.filter_auggie_suggestions(["python -m pytest tests/specific", "rm -rf /"])
    assert any("pytest" in c for c in safe)
    assert not any("rm" in c for c in safe)

def test_auggie_not_shell_true():
    """Verify subprocess is called without shell=True (AUG-306)."""
    cfg = _cfg()
    with patch("shutil.which", return_value="/usr/bin/auggie"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            worker = AuggieCliWorker(cfg)
            worker._execute("rid", "prompt", WorkerMode.READ_ONLY, None)
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("shell") is False or call_kwargs.get("shell") is not True
