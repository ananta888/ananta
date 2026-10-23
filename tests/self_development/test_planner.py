"""Pure planner parsing/rendering; no live model or network."""
import json

import pytest

from agent.services.self_development.planner import (
    HubModelContractPlanner,
    PlanningEnvelope,
    extract_json_object,
    render_planning_prompt,
)


class _Policy:
    value = {'allowed_roots': ['agent', 'tests'],
             'test_profiles': {'self-development': {'command': 'python -m pytest'}},
             'planning': {'enabled': True, 'max_files': 3,
                          'allowed_test_profiles': ['self-development'],
                          'default_test_profile': 'self-development',
                          'commit_scopes': ['autopilot', 'worker']}}


def test_extract_json_object_handles_fence_and_bare_text():
    assert extract_json_object('```json\n{"files": ["a"]}\n```') == {'files': ['a']}
    assert extract_json_object('prose {"files": ["a"]} trailing') == {'files': ['a']}


@pytest.mark.parametrize('text', ['', 'no json here', '{"unterminated"', '[1, 2]'])
def test_extract_json_object_rejects_missing_object(text):
    with pytest.raises(ValueError, match='planner_output'):
        extract_json_object(text)


def test_envelope_reads_operator_bounds_and_scope():
    envelope = PlanningEnvelope.from_policy(_Policy(), ['tests/a.py', 'agent/b.py'])
    assert envelope.allowed_roots == ('agent', 'tests')
    assert envelope.allowed_test_profiles == ('self-development',)
    assert envelope.default_test_profile == 'self-development'
    assert envelope.commit_scopes == ('autopilot', 'worker')
    assert envelope.max_files == 3
    assert envelope.scope_of('feat(worker): x'.ljust(20)) == 'worker'
    assert envelope.scope_of('not a commit') is None


def test_render_prompt_states_hard_constraints():
    envelope = PlanningEnvelope.from_policy(_Policy(), ['tests/a.py'])
    prompt = render_planning_prompt({'todo_id': 'T-1', 'title': 'T', 'description': 'D'}, envelope)
    assert 'agent, tests' in prompt
    assert 'at most 3 files' in prompt
    assert 'autopilot, worker' in prompt
    assert 'T-1' in prompt


def test_hub_model_planner_composes_generate_and_parse():
    captured = {}

    def generate(prompt):
        captured['prompt'] = prompt
        return 'here you go: ```json\n' + json.dumps({'files': ['tests/a.py']}) + '\n```'

    planner = HubModelContractPlanner(generate)
    envelope = PlanningEnvelope.from_policy(_Policy(), ['tests/a.py'])
    assert planner.propose(item={'todo_id': 'T-1'}, envelope=envelope) == {'files': ['tests/a.py']}
    assert 'T-1' in captured['prompt']
