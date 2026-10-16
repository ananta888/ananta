"""CodeCompass tool-use wiring for the self-development / worker mutation path."""
from types import SimpleNamespace

from agent.cli_backends.workspace_mutation import tool_actions
from agent.cli_backends.workspace_mutation.prompts import build_mode_instructions
from agent.services.codecompass_task_capability import ensure_tool_context, task_capability_scope
from agent.services.self_development.scope import required_worker_mutation

_CAPABILITY = {'token': 'signed', 'task_id': 'T-1', 'audience': 'http://worker'}


def test_ensure_tool_context_binds_capability_from_scope():
    with task_capability_scope(dict(_CAPABILITY)):
        cfg = ensure_tool_context({'resolved_mode': 'strict_patch_request'}, 'T-1')
    assert cfg['codecompass_capability'] == _CAPABILITY
    assert cfg['codecompass_task_id'] == 'T-1'
    assert cfg['resolved_mode'] == 'strict_patch_request'


def test_ensure_tool_context_keeps_existing_capability():
    cfg = ensure_tool_context({'codecompass_capability': {'token': 'own'}}, 'T-9')
    assert cfg['codecompass_capability'] == {'token': 'own'}
    assert cfg['codecompass_task_id'] == 'T-9'


def test_ensure_tool_context_without_scope_is_fail_closed():
    cfg = ensure_tool_context({}, 'T-2')
    assert 'codecompass_capability' not in cfg
    assert cfg['codecompass_task_id'] == 'T-2'


def test_task_identity_survives_missing_capability_without_granting_access():
    with task_capability_scope(None, task_id='T-missing-index'):
        cfg = ensure_tool_context({})
    assert cfg['codecompass_task_id'] == 'T-missing-index'
    assert 'codecompass_capability' not in cfg
    assert 'codecompass_task_id' not in ensure_tool_context({})


def test_nested_task_scope_restores_outer_identity():
    with task_capability_scope(dict(_CAPABILITY)):
        with task_capability_scope(None, task_id='T-inner'):
            assert ensure_tool_context({})['codecompass_task_id'] == 'T-inner'
        assert ensure_tool_context({})['codecompass_task_id'] == 'T-1'


def test_strict_patch_instructions_offer_codecompass_reads():
    instructions = build_mode_instructions('strict_patch_request')
    assert 'codecompass.plan_context' in instructions
    assert 'codecompass.search_symbols' in instructions
    assert 'codecompass.search' in instructions
    assert 'codecompass.get_file_context' in instructions


def test_controlled_workspace_instructions_offer_codecompass_reads():
    instructions = build_mode_instructions('controlled_workspace')
    assert 'codecompass.plan_context' in instructions
    assert 'codecompass.search_symbols' in instructions
    assert 'codecompass.search' in instructions


def test_mutation_scope_never_filters_tools():
    required = required_worker_mutation({
        'allowed_roots': ['agent', 'tests'],
        'test_profiles': {'a': {'command': 'pytest a'}},
    })
    assert set(required) == {'allowed_new_file_globs', 'allowlisted_test_commands'}


def test_plan_context_materializes_line_range_evidence(monkeypatch):
    calls = []

    def fake_execute(*, tool_name, arguments, workspace_dir, tool_call_id, config=None):
        calls.append((tool_name, arguments, tool_call_id))
        return {'status': 'ok', 'tool_name': tool_name, 'data': {'lines': arguments.get('line_start')}}

    monkeypatch.setattr(tool_actions, 'execute_ananta_tool', fake_execute)
    session = SimpleNamespace(workspace='/ws')
    result = {'status': 'ok', 'data': {'context_bundle': {'location_refs': [
        {'path': 'agent/a.py', 'line_start': 3, 'line_end': 9},
        {'path': 'agent/b.py', 'line_start': 1, 'line_end': 2},
        {'path': '', 'line_start': 1, 'line_end': 1},
    ]}}}
    tool_actions._materialize_context_ranges(session, result, 'tc-1', {'materialization_manifest': {}})
    assert [c[0] for c in calls] == ['repo.read_file_range', 'repo.read_file_range']
    assert calls[0][1] == {'path': 'agent/a.py', 'line_start': 3, 'line_end': 9}
    assert calls[0][2] == 'tc-1:range:1'
    materialized = result['data']['materialized_range_results']
    assert len(materialized) == 2
