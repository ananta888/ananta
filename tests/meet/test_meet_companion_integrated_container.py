"""Actual SQL/Hub/Worker HTTP/executor/browser/observation/terminal acceptance.

Explicit private gate: ephemeral synthetic project/policy and model/TTS only.
No runtime replacement, public deployment or production release claim.
"""

import base64
import hashlib
import json
import os
import selectors
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from flask import request
from werkzeug.serving import WSGIRequestHandler, make_server

from agent.repositories.meet_dialog_phases import TaskDialogPhases
from agent.services.meet_authorization_client import MeetAuthorizationClient
from agent.services.meet_companion_task_grants import MeetCompanionTaskGrants
from agent.services.meet_contract import MeetProfile
from agent.services.meet_dialog_phases import MeetDialogPhases
from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from agent.services.meet_media_transport import HttpMediaWorker
from agent.services.task_runtime_service import compare_and_set_local_task_status
from tests.meet.test_meet_companion_profile_dispatch import companion_system
from tests.meet_companion_build import require_current_browser_build
from tests.meet_companion_failure_observation import record_companion_cleanup_snapshot
from tests.meet_companion_gpu_observation import record_gpu_tts_receipts
from tests.meet_companion_real_model_observation import (
    expected_unique_replies,
    real_model_scope,
    record_real_model,
    start_real_model,
)
from tests.meet_companion_scratch_observation import require_retired_speech_scratch_cleanup
from tests.meet_dialog_browser_fixture import docker
from tests.meet_dialog_worker_container import DialogWorkerContainer
from tests.test_meet_dialog_cross_repository import close_bridge


def require_gpu_tts_scenario(assigned_model, scenario):
    enabled = os.environ.get('MEET_COMPANION_INTEGRATED_GPU_TTS') == '1'
    withdrawal = os.environ.get('MEET_COMPANION_INTEGRATED_GPU_WITHDRAWAL') == '1'
    allowed = {'answers-cancel', 'speech-mid-speech'} if withdrawal else {'answers-cancel'}
    if enabled and (not assigned_model or scenario not in allowed):
        pytest.skip('explicit GPU TTS gate requires an eligible assigned scenario')
    return enabled


def require_sync_receipts(container, scenario, *, gpu_tts=False):
    deadline = time.monotonic() + (120 if gpu_tts else 30)
    while True:
        sync = json.loads(docker('exec', container.name, 'python', '-S', '-c',
            "from pathlib import Path;p=Path('/state/synthetic-sync-observation.json');"
            "print(p.read_text() if p.exists() else '{}')"))
        assert sync.get('failed', 0) == 0, sync
        if scenario.startswith('speech-'):
            assert not sync
            if scenario.endswith('-mid-speech'):
                pushed = json.loads(docker('exec', container.name, 'python', '-S', '-c',
                    "from pathlib import Path;print(Path('/state/synthetic-push-observation.json').read_text())"))
                assert 0 < pushed['frames'] == pushed['retired_at'] < 200, pushed
            return
        if scenario.startswith('avatar-') and sync.get('retired') == 3:
            assert sync['completed'] == 0, sync
            return
        if sync.get('completed') == 3:
            assert sync['retired'] == 0 and sync['max_drift_us'] <= 41_667, sync
            return
        assert time.monotonic() < deadline, 'bounded synchronized speech completion missing'
        time.sleep(.1)


@pytest.mark.timeout(360 if os.environ.get('MEET_COMPANION_INTEGRATED_REAL_LLM') == '1'
                    and os.environ.get('MEET_COMPANION_INTEGRATED_GPU_TTS') == '1' else 240)
@pytest.mark.skipif(os.environ.get('MEET_COMPANION_INTEGRATED_GATE') != '1', reason='explicit private integrated gate')
@pytest.mark.parametrize('scenario', ['answers-cancel', 'model-cancel', 'complete', 'avatar-paused', 'speech-paused',
                                    'avatar-mid-speech', 'speech-mid-speech', 'assigned-answers-cancel',
                                    'assigned-model-cancel', 'assigned-complete', 'assigned-worker-crash',
                                    'assigned-avatar-paused', 'assigned-speech-paused',
                                    'assigned-avatar-mid-speech', 'assigned-speech-mid-speech',
                                    'assigned-speech-preparation-paused'])
def test_sql_assigned_companion_uses_actual_worker_and_browser(app, tmp_path, monkeypatch, scenario, record_property):
    assigned_model = scenario.startswith('assigned-')
    scenario = scenario.removeprefix('assigned-')
    gpu_tts = require_gpu_tts_scenario(assigned_model, scenario)
    actual_model = real_model_scope(assigned_model, scenario)
    actual_state = {}
    cancel_during_model = scenario in {'model-cancel', 'worker-crash'}
    pause_source = {'avatar-paused': 'avatar', 'avatar-mid-speech': 'avatar',
                    'speech-paused': 'speech', 'speech-mid-speech': 'speech',
                    'speech-preparation-paused': 'speech'}.get(scenario)
    root = Path(__file__).resolve().parents[2]
    meet = root.parent / 'webrtc-minimize-server'
    require_current_browser_build(meet, os.environ.get('MEET_TEST_PUBLIC_DIR'))
    key = Ed25519PrivateKey.generate()
    private, public, worker_key = (tmp_path / name for name in ('hub.pem', 'public.pem', 'worker-key'))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    hmac_key = b'synthetic-integrated-companion-private-key'
    worker_key.write_bytes(hmac_key)
    worker_key.chmod(0o600)
    tmp_path.chmod(0o755)
    counts = {'grant': 0, 'observe': 0, 'finish': 0, 'legacy': 0, 'observe_denied': 0, 'tool': 0, 'provider': 0}

    from agent.database import engine
    from tests.meet.meet_companion_integrated_models import assert_model_runs, register_provider

    register_provider(app, engine, counts, enabled=assigned_model,
                      actual_state=actual_state if actual_model else None)

    @app.before_request
    def remember_callback_action():
        if request.path.endswith('/dialog') and 0 < (request.content_length or 0) <= 16384:
            # The real handler consumes its raw body without caching. Record
            # only this closed test counter before it does so; never log input.
            action = (request.get_json(silent=True) or {}).get('action')
            request.environ['ananta.test.action'] = action if action in {'observe', 'finish'} else None

    @app.after_request
    def count_callbacks(response):
        if request.path == '/api/meet/v1/internal/companion':
            counts['legacy'] += 1
        if request.path.endswith('/dialog') and response.status_code != 200:
            if request.environ.get('ananta.test.action') == 'observe':
                counts['observe_denied'] += 1
        if response.status_code == 200:
            if request.path.endswith('/companion/task-grant'):
                counts['grant'] += 1
            elif request.path.endswith('/dialog'):
                action = request.environ.get('ananta.test.action')
                if action in counts:
                    counts[action] += 1
        return response

    with ExitStack() as cleanup:
        bridge = subprocess.Popen(['node', str(Path(__file__).with_name('meet_companion_live_bridge.mjs'))],
            env=os.environ | {'ANANTA_TEST_MEET_REPOSITORY': str(meet), 'MEET_TEST_HUB_PUBLIC_KEY': str(public)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cleanup.callback(close_bridge, bridge)

        def receive(timeout):
            with selectors.DefaultSelector() as selector:
                selector.register(bridge.stdout, selectors.EVENT_READ)
                assert selector.select(timeout), 'bounded integrated bridge observation timeout'
                line = bridge.stdout.readline(8193)
            assert line and len(line) <= 8192
            value = json.loads(line)
            assert 'error' not in value, value | {'callbacks': counts}
            return value

        ready = receive(60)
        network = json.loads(docker('network', 'inspect', ready['network']))[0]
        assert network['Internal'] is True
        start_real_model(cleanup, ready['network'], actual_state, enabled=actual_model)
        gateway = network['IPAM']['Config'][0]['Gateway']
        monkeypatch.setenv('SSL_CERT_FILE', ready['certificate'])
        certificate = x509.load_pem_x509_certificate(Path(ready['certificate']).read_bytes())
        spki = base64.b64encode(hashlib.sha256(certificate.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).digest()).decode()

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        monkeypatch.setitem(app.config, 'ROLE', 'hub')
        hub = make_server(gateway, 0, app, threaded=True, request_handler=Quiet)
        cleanup.callback(hub.server_close)
        cleanup.callback(hub.shutdown)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        hub_url = f'http://{gateway}:{hub.server_port}/api/meet/v1/internal/dialog'

        def companion_container(*arguments):
            # Only certificate and explicit model/TTS seams are test-mounted.
            if arguments[0] == 'create':
                values = list(arguments)
                source = str(root / 'tests/meet_worker_container_sitecustomize.py')
                for index, value in enumerate(values):
                    if value.startswith('type=bind,src=' + source + ','):
                        values[index] = value.replace(source, str(Path(__file__).with_name(
                            'meet_companion_container_sitecustomize.py')))
                index = values.index('--entrypoint=timeout')
                values[index:index] = ['--env=ANANTA_CPU_FALLBACK=' + ('0' if gpu_tts else '1'),
                                      '--env=MEET_COMPANION_NATIVE_SEQUENCE=' + os.environ.get(
                                          'MEET_COMPANION_NATIVE_SEQUENCE', '0'),
                                      '--env=MEET_AVATAR_SERVICE_ENABLED=0',
                                      '--env=MEET_AVATAR_IDLE_CLIP=/state/missing.mp4',
                                      '--env=TEST_LONG_SPEECH=' + ('1' if scenario.endswith('-mid-speech') else '0')]
                if assigned_model:
                    values[index:index] = ['--env=TEST_ASSIGNED_MODEL=1', '--env=MEET_LLM_BACKEND=ollama',
                        '--env=MEET_LLM_OPENAI_BASE_URL=' + f'http://{gateway}:{hub.server_port}/synthetic-model',
                        '--env=MEET_LLM_OPENAI_API_KEY=', '--env=MEET_COMPANION_LOG=/state/synthetic-dialog.log',
                        '--env=TEST_BLOCK_ASSIGNED_TTS=' + ('1' if scenario == 'speech-preparation-paused' else '0')]
                if gpu_tts:
                    from tests.meet_companion_gpu_resources import gpu_tts_arguments

                    values[values.index('--memory=1g')] = '--memory=4g'
                    index = values.index('--entrypoint=timeout')
                    values[index:index] = gpu_tts_arguments(root / 'data/meet-media/models',
                                                          os.environ['MEET_TEST_WSL_DRIVER_STORE'])
                arguments = tuple(values)
            return docker(*arguments)

        container = DialogWorkerContainer(ready['network'], os.environ['MEET_COMPANION_PACKAGED_IMAGE'],
                                          hub_url, lifetime=300, command=companion_container)
        cleanup.callback(container.close)
        container.start(worker_key, ready['certificate'], spki)
        with app.app_context():
            f = companion_system()
            caps = ['avatar.publish', 'chat.read', 'chat.send', 'speech.publish']
            f.f.authority.policies[('tenant', 'project')] = frozenset(caps)
            f.f.authority.clock = time.time
            f.service.clock = time.time
            f.f.binding.profile = MeetProfile(ready['origin'])
            f.f.binding.read.return_value = {'invite_url': f.f.binding.profile.invite(ready['room_id'])}
            f.tasks.publisher_url = container.origin
            f.service.issuer = issuer = MeetMachineGrantIssuer('https://synthetic-hub.example.test', private)
            f.service.meet = meet_client = MeetAuthorizationClient(f.f.authority, issuer)
            phases = MeetDialogPhases(TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status),
                                      f.f.authority, meet_client)
            f.service.phases = phases
            transport = HttpMediaWorker(container.origin + '/v1/turns', hmac_key)
            f.service.worker = MeetDialogWorkerRouter(f.f.authority, f.tasks,
                                                      {container.origin: transport}, container.origin)
            app.extensions.update(meet_binding_service=f.f.binding, meet_dialog_service=f.service,
                meet_media_worker_key=hmac_key,
                meet_companion_task_grants=MeetCompanionTaskGrants(f.f.authority, issuer))
            if assigned_model:
                from tests.meet.meet_companion_integrated_models import configure_models

                configure_models(app, f.f.authority, app.extensions['meet_companion_task_grants'], engine, counts,
                                 actual=actual_model)
            admitted = f.service.start(f.principal, 'project', f.payload | {
                'capabilities': caps, 'duration_seconds': 90 if scenario in {'complete', 'worker-crash'} else 300})
            f.service.control(f.principal, 'project', admitted['task_id'],
                              {'expected_revision': 1, 'chat': True, 'audio': False, 'screen': False, 'avatar': True})
        cleanup.callback(record_companion_cleanup_snapshot, container, engine, admitted['task_id'], counts,
                         record_property,
                         enabled=actual_model or os.environ.get('MEET_COMPANION_NATIVE_SEQUENCE') == '1')
        bridge_command = scenario if pause_source else (
            'hub-revoke' if cancel_during_model else 'backlog')
        bridge_command = ('assigned-' * assigned_model) + bridge_command
        bridge.stdin.write(bridge_command + '\n')
        bridge.stdin.flush()
        if pause_source:
            assert receive(60) == {'inputs_sent': 3}
            from tests.meet.meet_companion_integrated_models import wait_source_pause_checkpoint

            wait_source_pause_checkpoint(container, scenario, receive)
            with app.app_context():
                f.service.control(f.principal, 'project', admitted['task_id'], {
                    'expected_revision': 2, 'chat': True, 'audio': False, 'screen': False,
                    'avatar': pause_source != 'avatar', 'speech': pause_source != 'speech'})
        if cancel_during_model:
            assert receive(60) == {'inputs_sent': 3}
            from tests.meet.meet_companion_integrated_models import wait_model_started

            wait_model_started(container, counts, assigned=assigned_model)
            if scenario == 'worker-crash':
                from tests.meet.meet_companion_integrated_models import recover_crashed_worker

                recover_crashed_worker(app, engine, admitted['task_id'], container, counts, receive)
                return
        else:
            result = receive(300 if actual_model and gpu_tts else 180)
            audio_windows = result.pop('audio_windows')
            assert (audio_windows == 0 if scenario in {'speech-paused', 'speech-preparation-paused'}
                    else audio_windows > 0)
            assert result == {'replies': 3, 'unique': expected_unique_replies(result, actual=actual_model),
                              'captures': 0, 'transform_errors': 0,
                              'proxy_drops': 0, 'decoded_avatar': pause_source != 'avatar'}
            require_sync_receipts(container, scenario, gpu_tts=gpu_tts)
        with app.app_context():
            observed = phases.inspect(f.principal, 'project', admitted['task_id'])
            assert observed['phase'] in ({'joined', 'publishing'} if pause_source == 'avatar' else {'publishing'})
            task = f.tasks.get_by_id(admitted['task_id'])
            assert task.worker_execution_context['meet_phase']['membership'] is not None
            if scenario != 'complete':
                f.service.inspect(f.principal, 'project', admitted['task_id'], stop=True)
        if scenario == 'complete':
            deadline = time.monotonic() + 50
            while True:
                with app.app_context():
                    status = f.tasks.get_by_id(admitted['task_id']).status
                if status != 'in_progress':
                    assert status == 'completed'
                    break
                assert time.monotonic() < deadline, 'bounded assigned completion missing'
                time.sleep(.1)
        if cancel_during_model:
            assert receive(30) == {'revoked': True, 'replies': 0, 'camera_absent': True}
        deadline = time.monotonic() + 10
        while docker('exec', container.name, 'python', '-S', '-c',
                     "from pathlib import Path;print(Path('/state/synthetic-model-stopped').exists())") != 'True':
            assert time.monotonic() < deadline, 'bounded owned model cleanup missing'
            time.sleep(.1)
        with app.app_context():
            assert f.tasks.get_by_id(admitted['task_id']).status == (
                'completed' if scenario == 'complete' else 'cancelled')
        deadline = time.monotonic() + 5
        while transport.observe_dialog_resources()['slots']['active']:
            assert time.monotonic() < deadline, 'bounded executor slot cleanup missing'
            time.sleep(.1)
        assert counts['grant'] >= 2 and counts['observe'] >= 1 and counts['finish'] == 1 and counts['legacy'] == 0
        assert_model_runs(engine, admitted['task_id'], counts, cancel_during_model, enabled=assigned_model,
                          actual=actual_model)
        record_real_model(actual_state, counts, record_property, enabled=actual_model)
        record_gpu_tts_receipts(container, record_property, enabled=gpu_tts, scenario=scenario)
        require_retired_speech_scratch_cleanup(container, scenario)
