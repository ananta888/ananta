from unittest.mock import MagicMock, call, patch

from agent.services.commit_worker_service import CommitWorkerService


def make_task(commit_type="feat", commit_scope=None, subject_hint=None):
    return {
        "commit_metadata": {
            "commit_type": commit_type,
            "commit_scope": commit_scope,
            "commit_subject_hint": subject_hint,
        }
    }


def _mock_run(staged_files=None, commit_rc=0, commit_stderr=""):
    staged_output = "\n".join(staged_files or [])

    def side_effect(args, capture_output, text, cwd):
        result = MagicMock()
        if any("cached" in a for a in args):
            result.returncode = 0
            result.stdout = staged_output
            result.stderr = ""
        else:
            result.returncode = commit_rc
            result.stdout = "1 file changed"
            result.stderr = commit_stderr
        return result

    return side_effect


def test_empty_diff_returns_success_no_message():
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=[])
        result = CommitWorkerService().execute(make_task(), "/repo")
    assert result.success is True
    assert result.message is None


def test_valid_metadata_produces_correct_message():
    files = ["agent/services/goal_config_resolver_service.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        result = CommitWorkerService().execute(
            make_task(commit_type="feat", commit_scope="goal-config", subject_hint="add key allowlist"),
            "/repo",
        )
    assert result.success is True
    assert result.message == "feat(goal-config): add key allowlist"


def test_diff_scope_overrides_metadata_scope():
    files = ["agent/llm_integration.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        result = CommitWorkerService().execute(
            make_task(commit_type="fix", commit_scope="profiles", subject_hint="fix something"),
            "/repo",
        )
    assert result.scope_from_diff is True
    assert result.scope_confirmed == "llm"


def test_blocked_subject_hint_causes_failure():
    files = ["agent/services/goal_config_resolver_service.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        result = CommitWorkerService().execute(
            make_task(commit_type="feat", subject_hint="fixup planning"),
            "/repo",
        )
    assert result.success is False
    assert result.errors


def test_no_verify_flag_never_used():
    files = ["agent/llm_integration.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        CommitWorkerService().execute(make_task(subject_hint="fix something"), "/repo")
        for c in mock_run.call_args_list:
            assert "--no-verify" not in c.args[0]


def test_git_commit_not_called_when_validation_fails():
    files = ["agent/llm_integration.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        CommitWorkerService().execute(
            make_task(commit_type="feat", subject_hint="wip"),
            "/repo",
        )
        commit_calls = [
            c for c in mock_run.call_args_list if "commit" in c.args[0]
        ]
        assert len(commit_calls) == 0


def test_subprocess_nonzero_exit_returns_failure():
    files = ["agent/llm_integration.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(
            staged_files=files, commit_rc=1, commit_stderr="pre-commit hook failed"
        )
        result = CommitWorkerService().execute(make_task(subject_hint="fix llm usage"), "/repo")
    assert result.success is False
    assert "pre-commit hook failed" in result.errors[0]


def test_mixed_scope_uses_primary_scope():
    files = [
        "agent/services/goal_config_resolver_service.py",
        "agent/services/goal_config_resolver_service.py",
        "agent/services/config_profile_service.py",
    ]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(staged_files=files)
        result = CommitWorkerService().execute(
            make_task(commit_type="fix", subject_hint="fix resolver"),
            "/repo",
        )
    assert result.scope_confirmed == "goal-config"
