"""Stopped owned crash containers preserve SQL diagnostics without impossible exec."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests import meet_companion_failure_observation as observation


def test_stopped_container_keeps_authoritative_failed_run_state_without_exec(monkeypatch):
    calls = []

    def command(*arguments):
        calls.append(arguments)
        assert arguments == ('inspect', 'synthetic-owned-container', '--format', '{{.State.Running}}')
        return 'false'

    monkeypatch.setattr(observation, 'docker', command)
    database = MagicMock()
    connection = database.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalars.return_value.all.return_value = ['failed', 'succeeded']
    recorded = {}
    observation.record_companion_cleanup_snapshot(SimpleNamespace(name='synthetic-owned-container'), database,
        'synthetic-task', {'finish': 0}, lambda key, value: recorded.update({key: json.loads(value)}), enabled=True)
    result = recorded['companion_cleanup_snapshot']
    assert result['hub_run_states'] == {'failed': 1, 'succeeded': 1}
    assert result['container_running'] is False and result['browser_observation_available'] is False
    assert result['production_release_evidence'] is False and len(calls) == 1


def test_unknown_container_state_is_not_silently_a_successful_cleanup(monkeypatch):
    monkeypatch.setattr(observation, 'docker', lambda *arguments: 'unknown')
    with pytest.raises(ValueError, match='test_container_state_invalid'):
        observation.record_companion_cleanup_snapshot(SimpleNamespace(name='synthetic-owned-container'), MagicMock(),
            'synthetic-task', {}, lambda *arguments: None, enabled=True)
