from unittest.mock import MagicMock

from agent.services.commit_followup_service import maybe_create_git_commit_followup


def _queue():
    return MagicMock()


def _task(commit_metadata=None, task_kind="coding", auto_commit=False):
    return {
        "id": "task-abc",
        "task_kind": task_kind,
        "team_id": "team-1",
        "goal_id": "goal-1",
        "priority": "medium",
        "commit_metadata": commit_metadata,
        "effective_config": {
            "git_workspace": {"auto_commit": auto_commit}
        },
    }


def test_no_followup_without_commit_metadata():
    q = _queue()
    result = maybe_create_git_commit_followup(
        task=_task(commit_metadata=None, auto_commit=True),
        task_queue_service=q,
    )
    assert result is None
    q.ingest_task.assert_not_called()


def test_no_followup_when_auto_commit_false():
    meta = {"commit_type": "feat", "commit_scope": "llm"}
    q = _queue()
    result = maybe_create_git_commit_followup(
        task=_task(commit_metadata=meta, auto_commit=False),
        task_queue_service=q,
    )
    assert result is None
    q.ingest_task.assert_not_called()


def test_creates_followup_when_metadata_and_auto_commit():
    meta = {"commit_type": "feat", "commit_scope": "goal-config", "commit_subject_hint": "add key allowlist"}
    q = _queue()
    result = maybe_create_git_commit_followup(
        task=_task(commit_metadata=meta, auto_commit=True),
        task_queue_service=q,
    )
    assert result is not None
    assert result["task_kind"] == "git_commit"
    q.ingest_task.assert_called_once()
    call_kwargs = q.ingest_task.call_args.kwargs
    assert call_kwargs["extra_fields"]["task_kind"] == "git_commit"
    assert call_kwargs["extra_fields"]["commit_metadata"] == meta


def test_followup_has_correct_parent():
    meta = {"commit_type": "fix", "commit_scope": "llm"}
    q = _queue()
    maybe_create_git_commit_followup(
        task=_task(commit_metadata=meta, auto_commit=True),
        task_queue_service=q,
    )
    call_kwargs = q.ingest_task.call_args.kwargs
    assert call_kwargs["extra_fields"]["parent_task_id"] == "task-abc"


def test_non_coding_task_kind_blocked():
    meta = {"commit_type": "feat"}
    q = _queue()
    result = maybe_create_git_commit_followup(
        task=_task(commit_metadata=meta, task_kind="analysis", auto_commit=True),
        task_queue_service=q,
    )
    assert result is None
    q.ingest_task.assert_not_called()
