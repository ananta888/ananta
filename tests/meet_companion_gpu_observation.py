"""Closed actual GPU TTS and Task scratch-lifetime observations."""

import json

from tests.meet_dialog_browser_fixture import docker


def record_gpu_tts_receipts(container, record_property, *, enabled, scenario='answers-cancel'):
    if enabled:
        receipt = require_gpu_tts_receipts(container, scenario=scenario)
        record_property('actual_gpu_tts', json.dumps(receipt, sort_keys=True))


def require_gpu_tts_receipts(container, *, scenario='answers-cancel'):
    assert scenario in {'answers-cancel', 'speech-mid-speech'}, 'test_gpu_scenario_invalid'
    expected = 1 if scenario == 'speech-mid-speech' else 3
    values = json.loads(docker('exec', container.name, 'python', '-S', '-c',
        "from pathlib import Path; print(Path('/state/real-gpu-tts-observation.json').read_text())"))
    assert len(values) == expected and all(value['finished'] and value['samples'] > 0 for value in values), values
    if scenario == 'speech-mid-speech':
        # Reject a short substitute and prove withdrawal happened before half
        # of the actual long utterance had been submitted; never resume it.
        assert values[0]['samples'] > 220500, 'test_gpu_long_utterance_required'
        pushed = json.loads(docker('exec', container.name, 'python', '-S', '-c',
            "from pathlib import Path; print(Path('/state/synthetic-push-observation.json').read_text())"))
        assert type(pushed['frames']) is int and type(pushed['retired_at']) is int
        assert 0 < pushed['frames'] == pushed['retired_at'] < 200
        assert pushed['frames'] * 441 < values[0]['samples'] // 2
    assert len({value['cache'] for value in values}) == 1, values
    assert all(0 < value['seconds'] < 120 for value in values), values
    assert docker('exec', container.name, 'python', '-S', '-c',
        "from pathlib import Path; print(len(list(Path('/tmp').glob('ananta-meet-cuda-*'))))") == '0'
    return {'utterances': expected, 'seconds': [value['seconds'] for value in values],
            'samples': [value['samples'] for value in values], 'shared_task_cache': True,
            'cache_bytes': [value['cache_bytes'] for value in values],
            'task_cache_removed': True, 'production_release_evidence': False}
