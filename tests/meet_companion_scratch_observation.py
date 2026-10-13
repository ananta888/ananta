"""Task terminal cleanup observation for a genuinely interrupted TTS child."""

from tests.meet_dialog_browser_fixture import docker


def require_retired_speech_scratch_cleanup(container, scenario):
    if scenario != 'speech-preparation-paused':
        return
    assert docker('exec', container.name, 'python', '-S', '-c',
        "import json; from pathlib import Path; "
        "p=Path(json.loads(Path('/state/retired-speech-scratch.json').read_text())); "
        "assert p.name == 'speech.wav' and 'scratch' in p.parts; "
        "print(p.exists() or any(Path('/tmp').glob('ananta-meet-cuda-*')))") == 'False'
