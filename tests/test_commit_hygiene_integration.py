from unittest.mock import patch

from agent.services.commit_message_validator import CommitMessageValidator
from agent.services.commit_scope_resolver import CommitScopeResolver
from agent.services.commit_worker_service import CommitWorkerService


def _mock_run(staged_files, commit_rc=0):
    def side_effect(args, capture_output, text, cwd):
        from unittest.mock import MagicMock
        r = MagicMock()
        if any("cached" in a for a in args):
            r.returncode = 0
            r.stdout = "\n".join(staged_files)
            r.stderr = ""
        else:
            r.returncode = commit_rc
            r.stdout = "ok"
            r.stderr = "hook failed" if commit_rc != 0 else ""
        return r
    return side_effect


def test_full_path_feat_scope_resolves_and_commits():
    task = {
        "commit_metadata": {
            "commit_type": "feat",
            "commit_scope": "goal-config",
            "commit_subject_hint": "add key allowlist",
        }
    }
    files = ["agent/services/goal_config_resolver_service.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(files)
        result = CommitWorkerService().execute(task, "/repo")
    assert result.success is True
    assert result.message == "feat(goal-config): add key allowlist"


def test_blocked_message_from_old_session_rejected():
    task = {
        "commit_metadata": {
            "commit_type": "feat",
            "commit_scope": None,
            "commit_subject_hint": "fixup planning",
        }
    }
    files = ["agent/services/goal_config_resolver_service.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(files)
        result = CommitWorkerService().execute(task, "/repo")
    assert result.success is False
    assert any("blocked" in e for e in result.errors)


def test_scope_mismatch_corrected_from_diff():
    task = {
        "commit_metadata": {
            "commit_type": "fix",
            "commit_scope": "profiles",
            "commit_subject_hint": "fix something",
        }
    }
    files = ["agent/llm_integration.py"]
    with patch("agent.services.commit_worker_service.subprocess.run") as mock_run:
        mock_run.side_effect = _mock_run(files)
        result = CommitWorkerService().execute(task, "/repo")
    assert result.scope_from_diff is True
    assert result.scope_confirmed == "llm"
    assert "llm" in result.message


def test_validator_and_resolver_importable_independently():
    from agent.services.commit_message_validator import CommitMessageValidator
    from agent.services.commit_scope_resolver import CommitScopeResolver
    v = CommitMessageValidator()
    r = CommitScopeResolver()
    assert v.validate("feat(llm): fix something").valid is True
    assert r.resolve(["agent/llm_integration.py"]).primary_scope == "llm"
