from agent.services.commit_scope_resolver import CommitScopeResolver, FILE_TO_SCOPE_MAP


def resolver():
    return CommitScopeResolver()


def test_single_file_known_scope():
    r = resolver().resolve(["agent/services/goal_config_resolver_service.py"])
    assert r.primary_scope == "goal-config"
    assert r.is_mixed is False
    assert r.all_scopes == ["goal-config"]


def test_two_files_same_scope():
    r = resolver().resolve([
        "agent/services/goal_config_resolver_service.py",
        "agent/routes/tasks/goals.py",
    ])
    assert r.primary_scope == "goal-config"
    assert r.is_mixed is False


def test_two_files_mixed_scope():
    r = resolver().resolve([
        "agent/services/goal_config_resolver_service.py",
        "agent/services/config_profile_service.py",
    ])
    assert r.is_mixed is True
    assert "goal-config" in r.all_scopes
    assert "profiles" in r.all_scopes


def test_modelfile_scope():
    r = resolver().resolve([
        "scripts/ollama-autoimport.sh",
        "autoimport-state/modelfiles/ananta-default.Modelfile",
    ])
    assert r.primary_scope == "modelfile"
    assert r.is_mixed is False


def test_docs_scope():
    r = resolver().resolve(["AGENTS.md", "CONTRIBUTING.md"])
    assert r.primary_scope == "docs"
    assert r.is_mixed is False


def test_empty_list():
    r = resolver().resolve([])
    assert r.primary_scope is None
    assert r.all_scopes == []
    assert r.is_mixed is False
    assert r.unresolved_paths == []


def test_unknown_path():
    r = resolver().resolve(["some/new/module/thing.py"])
    assert r.primary_scope is None
    assert "some/new/module/thing.py" in r.unresolved_paths


def test_majority_wins_for_primary_scope():
    r = resolver().resolve([
        "agent/llm_integration.py",
        "agent/services/task_scoped_execution_service.py",
        "agent/services/config_profile_service.py",
    ])
    assert r.primary_scope == "llm"
    assert "profiles" in r.all_scopes


def test_custom_map_override():
    custom = (("foo/bar.py", "custom-scope"),)
    r = CommitScopeResolver(scope_map=custom).resolve(["foo/bar.py"])
    assert r.primary_scope == "custom-scope"


def test_file_to_scope_map_is_exported():
    assert isinstance(FILE_TO_SCOPE_MAP, tuple)
    assert len(FILE_TO_SCOPE_MAP) > 0


def test_planning_service_maps_to_planning_scope():
    r = resolver().resolve(["agent/services/planning_service.py"])
    assert r.primary_scope == "planning"


def test_context_delivery_service_maps_to_context_scope():
    r = resolver().resolve(["agent/services/context_delivery_service.py"])
    assert r.primary_scope == "context"


def test_scope_resolver_drift_flags_unknown_new_service_path():
    r = resolver().resolve(["agent/services/new_unmapped_service.py"])
    assert r.primary_scope is None
    assert "agent/services/new_unmapped_service.py" in r.unresolved_paths
