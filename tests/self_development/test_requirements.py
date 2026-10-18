"""Source requirements survive catalog import without acquiring execution rights."""
import json
from pathlib import Path

import pytest

from agent.services.self_development.catalog import import_document
from agent.services.self_development.contracts import form
from agent.services.self_development.policy import DevelopmentPolicy


@pytest.fixture
def development_policy(tmp_path: Path) -> DevelopmentPolicy:
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({
        'enabled': True, 'automatic_publication': True,
        'remote_url': 'https://github.com/ananta888/ananta.git',
        'branch': 'ananta/self-development',
        'control_root': str(tmp_path / 'control'),
        'workspace_root': str(tmp_path / 'workers'),
        'allowed_roots': ['tests'],
        'test_profiles': {'self-development': {'command': 'python -m pytest'}},
    }))
    return DevelopmentPolicy(path)


def test_import_preserves_structured_requirements_when_source_has_goal_context(
    development_policy: DevelopmentPolicy,
) -> None:
    # Given a task whose requirements cannot be inferred from its short title.
    document = form()
    document.update(goal='Evaluate memory behavior', purpose='Measure before adoption')
    requirements = {
        'acceptance_criteria': [
            {'id': 'cases', 'minimum': 30, 'language': 'de'},
            'Compare against the current adapter',
        ],
        'references': [{'path': 'agent/services/memory.py', 'symbols': ['retrieve']}],
        'type': 'research',
        'goal': 'Establish the baseline',
    }
    document['tasks'][0].update(requirements)

    # When the Hub imports the source for planning and assignment.
    item = import_document(document, 'todos/memory.json', development_policy)[0]

    # Then all requirements and their structure remain available.
    assert {key: item[key] for key in requirements} == requirements
    assert item['document_goal'] == document['goal']
    assert item['document_purpose'] == document['purpose']
    assert item['status'] == 'ready'
    document['tasks'][0]['references'][0]['symbols'].append('changed_after_import')
    assert item['references'][0]['symbols'] == ['retrieve']


def test_import_keeps_source_block_when_requirements_are_present(
    development_policy: DevelopmentPolicy,
) -> None:
    # Given a blocked task with a valid, pre-authorized execution contract.
    document = form()
    document['tasks'][0].update(status='blocked', acceptance_criteria=['Run 30 cases'])

    # When it is imported.
    item = import_document(document, 'todos/memory.json', development_policy)[0]

    # Then forwarding its requirements grants no dispatch authorization.
    assert item['acceptance_criteria'] == ['Run 30 cases']
    assert (item['status'], item['reason']) == ('blocked', 'source_task_blocked')


def test_import_supports_legacy_task_without_new_requirement_fields(
    development_policy: DevelopmentPolicy,
) -> None:
    # Given the existing execution-contract form without richer metadata.
    document = form()

    # When it is imported.
    item = import_document(document, 'user-submission', development_policy)[0]

    # Then legacy authorization and empty context remain explicit.
    assert item['status'] == 'ready'
    assert item['acceptance_criteria'] == []
    assert item['references'] == []
    assert item['document_goal'] == ''
    assert item['document_purpose'] == ''


@pytest.mark.parametrize('criteria, reason', [
    (['x' * 65536], 'task_requirements_too_large'),
    ([float('nan')], 'invalid_task_requirements'),
])
def test_import_blocks_requirements_that_cannot_be_forwarded_intact(
    development_policy: DevelopmentPolicy, criteria: list[str | float], reason: str,
) -> None:
    # Given requirements outside the finite JSON assignment envelope.
    document = form()
    document['tasks'][0]['acceptance_criteria'] = criteria

    # When the Hub imports them.
    item = import_document(document, 'user-submission', development_policy)[0]

    # Then the task is blocked instead of silently losing acceptance criteria.
    assert (item['status'], item['reason']) == ('blocked', reason)
