from agent.services.commit_metadata_inferrer import CommitMetadataInferrer


def infer(**kwargs):
    return CommitMetadataInferrer().infer(**kwargs)


def test_fix_type_for_bugfix_task():
    meta = infer(
        description="Fix the checksum computation in goal_config_resolver_service.py",
        task_kind="coding",
    )
    assert meta.commit_type == "fix"


def test_fix_type_for_bug_keyword():
    meta = infer(description="Resolve bug in profile loader", task_kind="coding")
    assert meta.commit_type == "fix"


def test_feat_type_for_new_feature():
    meta = infer(description="Add new endpoint for effective config", task_kind="coding")
    assert meta.commit_type == "feat"


def test_security_type_overrides_others():
    meta = infer(description="Add security redaction for token fields", task_kind="coding")
    assert meta.commit_type == "security"


def test_test_kind_gives_test_type():
    meta = infer(description="Write tests for resolver", task_kind="test")
    assert meta.commit_type == "test"


def test_doc_kind_gives_docs_type():
    meta = infer(description="Document goal config API", task_kind="doc")
    assert meta.commit_type == "docs"


def test_scope_inferred_from_file_reference():
    meta = infer(
        description="Fix checksum in agent/services/goal_config_resolver_service.py",
        task_kind="coding",
    )
    assert meta.commit_scope == "goal-config"


def test_scope_none_when_no_file_reference():
    meta = infer(description="Fix a bug in the system", task_kind="coding")
    assert meta.commit_scope is None


def test_ambiguous_task_leaves_scope_none():
    meta = infer(description="Update various modules across the codebase")
    assert meta.commit_scope is None


def test_subject_hint_from_title():
    meta = infer(
        description="Long description...",
        task_kind="coding",
        title="Add ALLOWED_GOAL_CONFIG_KEYS export",
    )
    assert meta.commit_subject_hint == "Add ALLOWED_GOAL_CONFIG_KEYS export"


def test_rückwärtskompatibel_no_exception_without_task_kind():
    meta = infer(description="update dependencies")
    assert meta is not None
    assert meta.commit_type is not None
