from agent.models import CommitMetadata, TaskCreateRequest


def test_commit_metadata_defaults_to_none():
    req = TaskCreateRequest(goal_id="g1", description="fix bug")
    assert req.commit_metadata is None


def test_commit_metadata_fields_preserved():
    meta = CommitMetadata(
        commit_type="feat",
        commit_scope="goal-config",
        commit_subject_hint="add key allowlist",
    )
    data = meta.model_dump()
    restored = CommitMetadata(**data)
    assert restored.commit_type == "feat"
    assert restored.commit_scope == "goal-config"
    assert restored.commit_subject_hint == "add key allowlist"


def test_commit_metadata_partial_fields_ok():
    meta = CommitMetadata(commit_type="fix")
    assert meta.commit_scope is None
    assert meta.commit_subject_hint is None


def test_existing_task_create_without_commit_metadata_unchanged():
    req = TaskCreateRequest(description="do something")
    assert req.commit_metadata is None
    assert req.task_kind is None


def test_task_create_with_commit_metadata():
    meta = CommitMetadata(commit_type="feat", commit_scope="llm")
    req = TaskCreateRequest(description="add feature", commit_metadata=meta)
    assert req.commit_metadata is not None
    assert req.commit_metadata.commit_type == "feat"
    assert req.commit_metadata.commit_scope == "llm"
