"""Synthetic validation ports cannot turn short or resumed speech into a GPU withdrawal success."""

import json
from types import SimpleNamespace

import pytest

from tests import meet_companion_gpu_observation as observation


def fixture(monkeypatch, *, utterances=1, samples=300000, frames=3, retired_at=3):
    values = [{'finished': True, 'samples': samples, 'seconds': 44,
               'cache': '/tmp/synthetic-task-cache', 'cache_bytes': 10}] * utterances

    def command(*arguments):
        script = arguments[-1]
        if 'real-gpu-tts-observation.json' in script:
            return json.dumps(values)
        if 'synthetic-push-observation.json' in script:
            return json.dumps({'frames': frames, 'retired_at': retired_at})
        if "glob('ananta-meet-cuda-*')" in script:
            return '0'
        raise AssertionError('test_observation_command_unexpected')

    monkeypatch.setattr(observation, 'docker', command)
    return SimpleNamespace(name='synthetic-owned-container')


def test_long_gpu_withdrawal_requires_one_utterance_and_an_early_permanent_boundary(monkeypatch):
    container = fixture(monkeypatch)
    receipt = observation.require_gpu_tts_receipts(container, scenario='speech-mid-speech')
    assert receipt['utterances'] == 1 and receipt['samples'] == [300000]
    assert receipt['production_release_evidence'] is False


@pytest.mark.parametrize('options', [
    {'samples': 220500}, {'utterances': 2}, {'frames': 3, 'retired_at': 4},
    {'frames': 200, 'retired_at': 200}, {'frames': True, 'retired_at': True},
])
def test_short_resumed_or_unverified_boundary_cannot_pass(monkeypatch, options):
    container = fixture(monkeypatch, **options)
    with pytest.raises(AssertionError):
        observation.require_gpu_tts_receipts(container, scenario='speech-mid-speech')


def test_normal_gpu_acceptance_still_requires_all_three_utterances(monkeypatch):
    container = fixture(monkeypatch, utterances=3)
    assert observation.require_gpu_tts_receipts(container)['utterances'] == 3
    container = fixture(monkeypatch)
    with pytest.raises(AssertionError):
        observation.require_gpu_tts_receipts(container)


def test_unrelated_scenario_cannot_weaken_gpu_receipts(monkeypatch):
    with pytest.raises(AssertionError, match='test_gpu_scenario_invalid'):
        observation.require_gpu_tts_receipts(fixture(monkeypatch), scenario='speech-paused')
