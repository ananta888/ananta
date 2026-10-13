"""Actual packaged run; synthetic model/TTS only, real browser and CPU media."""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import BrowserType

from worker.meet_media import companion
from worker.meet_media.companion_execution_process import CompanionExecutionProcess

# The ephemeral input is synthetic; the actual Worker key loader still sees
# an owner-only file created by its nonroot user, as in the production contract.
key = Path('/state/worker-key')
key.write_bytes(Path('/test/key').read_bytes())
key.chmod(0o600)
os.environ['MEET_WORKER_KEY_FILE'] = str(key)

MODEL = r'''
import base64,json,math,struct,sys,time
from pathlib import Path
answered=0
for line in sys.stdin:
    request=json.loads(line)
    if request['kind']=='answer':
        answered+=1
        Path('/state/synthetic-model-started').touch()
        time.sleep(50 if answered==1 else .1)
        label='recovery' if 'synthetic recovery' in request['text'] else 'companion'
        value='Synthetic '+label+' answer '+str(answered)
    else:
        pcm=b''.join(struct.pack('<h',int(3000*math.sin(2*math.pi*440*n/22050))) for n in range(11025))
        value=base64.b64encode(pcm).decode()
    print(json.dumps({'kind':request['kind'],'result':value}),flush=True)
'''
launch = BrowserType.launch


def private_launch(self, *args, **kwargs):
    assert kwargs['chromium_sandbox'] is True
    kwargs['args'] = [*kwargs['args'], '--ignore-certificate-errors-spki-list=' + os.environ['TEST_CERT_SPKI']]
    return launch(self, *args, **kwargs)


BrowserType.launch = private_launch
executions = []


def execution(**options):
    instance = CompanionExecutionProcess(command=(sys.executable, '-c', MODEL), **options)
    executions.append(instance)
    return instance


companion.CompanionExecutionProcess = execution
if os.environ.get('TEST_ASSIGNED_COMPANION') == '1':
    from worker.meet_media import companion_task_runtime
    from worker.meet_media.companion_task_controls import CompanionControlLost
    from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
    from worker.meet_media.dialog_session_operations import DialogSessionOperations

    phase = ['browser_setup']
    ready_method, join_method, read_method = (DialogSessionOperations.ready, DialogSessionOperations.join,
                                             AssignedCompanionGrantClient.read)

    def ready_probe(self, *args, **kwargs):
        phase[0] = 'ready'
        result = ready_method(self, *args, **kwargs)
        phase[0] = 'ready_done'
        return result

    def join_probe(self, *args, **kwargs):
        phase[0] = 'join'
        result = join_method(self, *args, **kwargs)
        phase[0] = 'joined'
        return result

    def read_probe(self):
        phase[0] = 'grant_read'
        result = read_method(self)
        phase[0] = 'grant_read_done'
        return result

    DialogSessionOperations.ready = ready_probe
    DialogSessionOperations.join = join_probe
    AssignedCompanionGrantClient.read = read_probe

    companion_task_runtime.CompanionExecutionProcess = execution
    assignment = json.loads(Path('/test/assignment').read_text())
    client = AssignedCompanionGrantClient(assignment, os.environ['MEET_HUB_COMPANION_TASK_URL'], key.read_bytes())
    try:
        companion_task_runtime.run(client, log=companion.log)
    except CompanionControlLost:
        pass  # Expected test-owned Hub cancellation, not an authority fallback.
    except Exception as error:
        known = {"meet_client_probe_invalid", "meet_dialog_session_checkpoint_required",
                 "meet_dialog_session_operation_failed", "meet_browser_network_origin_invalid",
                 "meet_client_probe_unsupported", "meet_machine_navigation_denied", "meet_dialog_session_expired"}
        code = str(error) if str(error) in known else "assigned_runtime_setup_failed"
        kind = type(error).__name__
        print(json.dumps({"assigned_failure": code, "phase": phase[0],
                          "kind": kind if kind in {'Error', 'TimeoutError', 'ValueError', 'KeyError', 'TypeError'}
                          else 'other'}), flush=True)
        raise SystemExit(1) from None
else:
    companion.run()
assert len(executions) == 1
assert executions[0]._process is not None and executions[0]._process.poll() is not None
receipt = {"owned_model_process_stopped": True}
if os.environ.get('TEST_SOURCE_WITHDRAWAL') == '1':
    diagnostic = Path('/state/companion.log').read_text()
    receipt.update(recv_count=diagnostic.count('RECV'), reply_count=diagnostic.count('REPLY'))
print(json.dumps(receipt), flush=True)
