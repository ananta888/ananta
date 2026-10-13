"""Private certificate/model/TTS seams; actual server, executor and browser run.

Loaded only from the explicit test mount, never from the Worker image. Model
responses and CPU tone are synthetic and cannot establish production evidence.
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import BrowserType

from worker.meet_media import companion_execution_process
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted
from worker.meet_media.companion_task_speech import AssignedCompanionSpeech
from worker.meet_media.dialog_session_operations import DialogSessionOperations

pin = os.environ['MEET_TEST_BROWSER_SPKI']
if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', pin):
    raise ValueError('test_certificate_pin_invalid')
launch = BrowserType.launch


def private_launch(self, *args, **kwargs):
    assert not args and kwargs == {'headless': True, 'chromium_sandbox': True,
                                  'args': ['--autoplay-policy=no-user-gesture-required']}
    return launch(self, **(kwargs | {'args': [*kwargs['args'], '--ignore-certificate-errors-spki-list=' + pin]}))


BrowserType.launch = private_launch

# Keep only a bounded machine error code when an installed native join fails.
# The wrapper rethrows unchanged and does not alter authority or retry work.
native_join = DialogSessionOperations.join


def observed_join(self, *args, **kwargs):
    self.page.evaluate("""() => {
      const join = window.anantaMachine.join;
      window.anantaMachine.join = async (...args) => {
        try { return await join(...args); }
        catch (error) {
          window.__testJoinCode = /^[a-z_]{1,96}$/.test(error.message) ? error.message : 'unclassified';
          throw error;
        }
      };
    }""")
    try:
        return native_join(self, *args, **kwargs)
    except Exception:
        value = self.page.evaluate("() => window.__testJoinCode ?? 'unsettled'")
        Path('/state/synthetic-session-observation.json').write_text(json.dumps({'join_error': value}))
        raise


DialogSessionOperations.join = observed_join
MODEL = r'''
import base64,json,math,os,struct,sys,time
from pathlib import Path
answered=0
for line in sys.stdin:
    request=json.loads(line)
    if request['kind']=='answer':
        answered+=1
        Path('/state/synthetic-model-started').touch()
        time.sleep(50 if answered==1 else .1)
        value='Synthetic companion answer '+str(answered)
    else:
        samples=88200 if os.environ.get('TEST_LONG_SPEECH')=='1' else 11025
        pcm=b''.join(struct.pack('<h',int(3000*math.sin(2*math.pi*440*n/22050))) for n in range(samples))
        value=base64.b64encode(pcm).decode()
    print(json.dumps({'kind':request['kind'],'result':value}),flush=True)
'''


class SyntheticModelProcess(CompanionExecutionProcess):
    """Same owned pipe/process lifecycle; only model/TTS response implementation differs."""

    def __init__(self, **options):
        if os.environ.get('TEST_ASSIGNED_MODEL') == '1':
            super().__init__(**options)
        else:
            super().__init__(command=(sys.executable, '-S', '-c', MODEL), **options)

    def close(self):
        super().close()
        if self._process is not None and self._process.poll() is not None:
            Path('/state/synthetic-model-stopped').touch()


# The executor uses ``python -m``: patch the imported adapter symbol so its
# __main__ module receives the seam too, without pre-importing that entrypoint.
companion_execution_process.CompanionExecutionProcess = SyntheticModelProcess

if os.environ.get('TEST_ASSIGNED_MODEL') == '1' and os.environ.get('TEST_REAL_ASSIGNED_TTS') != '1':
    import math
    import struct

    from worker.meet_media import companion

    def synthetic_pcm(_text):
        if os.environ.get('TEST_BLOCK_ASSIGNED_TTS') == '1':
            import tempfile
            import time

            temporary = tempfile.mkdtemp(prefix='synthetic-retired-speech-')
            target = Path(temporary) / 'speech.wav'
            target.write_bytes(b'synthetic-private-retired-audio')
            Path('/state/retired-speech-scratch.json').write_text(json.dumps(str(target)))
            Path('/state/synthetic-tts-started').touch()
            time.sleep(60)
        samples = 88200 if os.environ.get('TEST_LONG_SPEECH') == '1' else 11025
        return b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * n / 22050)))
                        for n in range(samples))

    companion.synthesize_pcm = synthetic_pcm

if os.environ.get('TEST_REAL_ASSIGNED_TTS') == '1':
    import time

    from worker.meet_media import companion

    native_pcm = companion.synthesize_pcm
    observation_path = Path('/state/real-gpu-tts-observation.json')

    def observed_real_pcm(text):
        observations = json.loads(observation_path.read_text()) if observation_path.exists() else []
        observation = {'started': True, 'finished': False, 'cache': os.environ['CUDA_CACHE_PATH']}
        observations.append(observation)
        observation_path.write_text(json.dumps(observations))
        started = time.monotonic()
        try:
            pcm = native_pcm(text)
            observation.update(finished=True, samples=len(pcm) // 2, seconds=time.monotonic() - started)
            return pcm
        finally:
            cache = Path(observation['cache'])
            observation['cache_bytes'] = sum(path.stat().st_size for path in cache.rglob('*') if path.is_file())
            observation_path.write_text(json.dumps(observations))

    companion.synthesize_pcm = observed_real_pcm

native_publication = AssignedCompanionSpeech.publish
sync_observation = {"completed": 0, "retired": 0, "failed": 0, "max_drift_us": 0}


def observe_publication(self, *args, **kwargs):
    try:
        result = native_publication(self, *args, **kwargs)
    except Exception as error:
        sync_observation["failed"] += 1
        code = re.search(r'\bmeet_[a-z0-9_]{1,96}\b', str(error))
        sync_observation["failure_code"] = code.group(0) if code else type(error).__name__
        Path('/state/synthetic-sync-observation.json').write_text(json.dumps(sync_observation))
        raise
    report = result[1]
    sync_observation["completed" if report.get("sync_complete", True) else "retired"] += 1
    sync_observation["max_drift_us"] = max(sync_observation["max_drift_us"], report["max_drift_us"])
    Path('/state/synthetic-sync-observation.json').write_text(json.dumps(sync_observation))
    return result


AssignedCompanionSpeech.publish = observe_publication

from worker.meet_media.snake_avatar_timeline import SpeechMediaTimeline  # noqa: E402

native_drift = SpeechMediaTimeline.measure_drift


def observe_drift(self, **kwargs):
    segments = kwargs.get('segments', self.segments())
    anchor = kwargs['anchor_us']
    lead = kwargs.get('video_lead_us', 0)
    values = [{'segment': segment.index,
               'video_delta_us': video - max(anchor, anchor + segment.start_us(self.rate) - lead),
               'audio_lead_us': anchor + segment.start_us(self.rate) - audio}
              for segment, video, audio in zip(segments, kwargs['video_started_us'], kwargs['audio_pushed_us'])]
    path = Path('/state/synthetic-drift-observation.json')
    observations = json.loads(path.read_text()) if path.exists() else []
    observations.append(values)
    path.write_text(json.dumps(observations))
    return native_drift(self, **kwargs)


SpeechMediaTimeline.measure_drift = observe_drift

if os.environ.get('TEST_REAL_ASSIGNED_TTS') == '1':
    from worker.meet_media.companion_chat_pump import CompanionChatPump

    native_chat_step = CompanionChatPump.step
    renewal_observation = {'due_attempts': 0, 'deferred_attempts': 0, 'satisfied_attempts': 0,
                           'unchanged_success_clock_during_deferral': True}

    def observe_chat_step(self):
        previous = self.last_renew
        next_attempt = getattr(self, 'next_renew_attempt', 0)
        due = self.clock() - previous >= self.renew_interval and self.clock() >= next_attempt
        result = native_chat_step(self)
        if due:
            renewal_observation['due_attempts'] += 1
            if getattr(self, 'next_renew_attempt', 0) > next_attempt:
                renewal_observation['deferred_attempts'] += 1
                renewal_observation['unchanged_success_clock_during_deferral'] &= self.last_renew == previous
            elif self.last_renew > previous:
                renewal_observation['satisfied_attempts'] += 1
            Path('/state/synthetic-renewal-observation.json').write_text(json.dumps(renewal_observation))
        return result

    CompanionChatPump.step = observe_chat_step

native_push, native_guard = AssignedCompanionSpeech._push_speech, AssignedCompanionSpeech._guard
push_observation = {"frames": 0, "retired_at": None}


def observe_push(self, *args, **kwargs):
    result = native_push(self, *args, **kwargs)
    push_observation["frames"] += 1
    Path('/state/synthetic-push-observation.json').write_text(json.dumps(push_observation))
    return result


def observe_guard(self):
    try:
        return native_guard(self)
    except CompanionSourceInterrupted:
        if push_observation["retired_at"] is None:
            push_observation["retired_at"] = push_observation["frames"]
            Path('/state/synthetic-push-observation.json').write_text(json.dumps(push_observation))
        raise


AssignedCompanionSpeech._push_speech, AssignedCompanionSpeech._guard = observe_push, observe_guard
