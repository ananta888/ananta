"""Real bounded subprocesses; no provider, TTS device or interactive approval."""

import sys
import time
from pathlib import Path

import pytest

from worker.meet_media.companion_execution_process import CompanionExecutionError, CompanionExecutionProcess
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionSourceInterrupted

pytestmark = pytest.mark.timeout(10)


def runner(code, **options):
    return CompanionExecutionProcess(command=(sys.executable, "-u", "-c", code), **options)


@pytest.mark.parametrize("loss", [CompanionSourceInterrupted, CompanionControlLost])
def test_assigned_tts_has_separate_lifetime_and_source_pause_preserves_model(tmp_path, loss):
    marker = tmp_path / "synthetic-tts-started"
    code = f"""
import json,sys,time
from pathlib import Path
count=0
for line in sys.stdin:
    request=json.loads(line)
    if request['kind']=='speech':
        Path({str(marker)!r}).touch()
        time.sleep(30)
        value='AAE='
    else:
        count+=1
        value=str(count)
    print(json.dumps({{'kind':request['kind'],'result':value}}),flush=True)
"""

    def forbidden(*_args, **_kwargs):
        pytest.fail("synthetic TTS must not receive model or tool authority")

    execution = runner(code, model_profile={"backend": "openai", "model": "synthetic-model",
        "environment_digest": "e" * 64}, ports={"model": forbidden, "tool": forbidden})
    speech_processes = []
    try:
        assert execution.call("answer", "synthetic first", cooperate=lambda: None) == "1"
        model = execution._process

        def interrupt():
            if marker.exists():
                speech_processes.append(execution._speech_transport.process)
                raise loss("synthetic_control_change")

        with pytest.raises(loss, match="synthetic_control_change"):
            execution.call("speech", "synthetic first", cooperate=interrupt)
        assert len(speech_processes) == 1 and speech_processes[0].poll() is not None
        assert speech_processes[0] is not model and execution._speech_transport is None
        if loss is CompanionSourceInterrupted:
            assert model.poll() is None
            assert execution.call("answer", "synthetic next", cooperate=lambda: None) == "2"
            assert execution._process is model
        else:
            assert model.poll() is not None
            for kind in ("answer", "speech"):
                with pytest.raises(CompanionExecutionError, match="execution_closed"):
                    execution.call(kind, "synthetic next", cooperate=lambda: None)
            assert execution._speech_transport is None
    finally:
        execution.close()


def test_one_owned_process_retains_execution_state_while_owner_cooperates():
    execution = runner("""
import json, sys, time
count = 0
for line in sys.stdin:
    request = json.loads(line)
    count += 1
    time.sleep(0.15)
    print(json.dumps({'kind': request['kind'], 'result': str(count)}), flush=True)
""")
    ticks = []
    try:
        assert execution.call("answer", "synthetic", cooperate=lambda: ticks.append(True)) == "1"
        process = execution._process
        assert execution.call("answer", "synthetic again", cooperate=lambda: ticks.append(True)) == "2"
        assert execution._process is process and len(ticks) >= 4
    finally:
        execution.close()
    assert process.poll() is not None


def test_negotiated_profile_preserves_full_unicode_message_character_budget():
    execution = runner("""
import json,sys
request=json.loads(sys.stdin.readline())
assert len(request['text'])==2000 and request['model_execution']['backend']=='openai'
print(json.dumps({'kind':'answer','result':'synthetic'}),flush=True)
""", model_profile={"backend": "openai", "model": "synthetic", "environment_digest": "e" * 64},
        ports={"tool": lambda *args, **kwargs: None, "model": lambda *args, **kwargs: None})
    try:
        assert execution.call("answer", "🐍" * 2000, cooperate=lambda: None) == "synthetic"
    finally:
        execution.close()


def test_control_loss_cancels_process_and_cannot_restart_or_repeat_operation():
    execution = runner("import time; time.sleep(20)")

    def revoked():
        raise PermissionError("synthetic_lease_revoked")

    with pytest.raises(PermissionError, match="lease_revoked"):
        execution.call("answer", "synthetic", cooperate=revoked)
    assert execution._process.poll() is not None
    with pytest.raises(CompanionExecutionError, match="execution_closed"):
        execution.call("answer", "synthetic", cooperate=lambda: None)


def test_timeout_kills_child_ignoring_termination():
    execution = runner("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)",
                       timeout_seconds=0.2)
    with pytest.raises(CompanionExecutionError, match="execution_timeout"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


@pytest.mark.parametrize("code", [
    "print('{\"kind\":\"speech\",\"result\":\"foreign\"}', flush=True)",
    "print('{\"kind\":\"answer\",\"result\":\"x\",\"authority\":true}', flush=True)",
])
def test_wrong_or_broadened_result_never_reaches_browser(code):
    execution = runner(code)
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_unbounded_output_is_stopped_before_receipt():
    execution = runner("import sys; sys.stdout.write('x' * 3000000); sys.stdout.flush()")
    with pytest.raises(CompanionExecutionError, match="output_limit"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_invalid_operation_and_text_never_spawn():
    execution = runner("raise AssertionError('must not execute')")
    for kind, text in [("delegate", "synthetic"), ("answer", "x" * 2001), ("speech", "x" * 451)]:
        with pytest.raises(ValueError, match="request_invalid"):
            execution.call(kind, text, cooperate=lambda: None)
    assert execution._process is None


def test_control_loss_terminates_spawned_descendant_as_well(tmp_path):
    marker = tmp_path / "synthetic-child-pid"
    execution = runner(f"""
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
Path({str(marker)!r}).write_text(str(child.pid))
time.sleep(20)
""", timeout_seconds=2)

    def revoked_after_spawn():
        if marker.exists():
            raise PermissionError("synthetic_lease_revoked")

    with pytest.raises(PermissionError, match="lease_revoked"):
        execution.call("answer", "synthetic", cooperate=revoked_after_spawn)
    pid = int(marker.read_text())
    state = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 1
    while state.exists() and state.read_text().split()[2] != "Z" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not state.exists() or state.read_text().split()[2] == "Z"


def test_pcm_is_validated_and_decoded_before_browser_receives_it():
    execution = runner("print('{\"kind\":\"speech\",\"result\":\"AAE=\"}', flush=True)")
    try:
        assert execution.call("speech", "synthetic", cooperate=lambda: None) == b"\x00\x01"
    finally:
        execution.close()
    execution = runner("print('{\"kind\":\"speech\",\"result\":\"not pcm\"}', flush=True)")
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("speech", "synthetic", cooperate=lambda: None)


def test_real_process_is_killed_when_cooperative_pump_loses_session():
    from worker.meet_media.companion_chat_inbox import CompanionChatInbox
    from worker.meet_media.companion_chat_pump import CompanionChatPump, CompanionSessionEnded

    checks = []

    def status():
        checks.append(True)
        return {"joined": False}

    inbox = CompanionChatInbox()
    pump = CompanionChatPump(inbox, status=status, open_chat=lambda: pytest.fail("no open after loss"),
                             poll=lambda _cursor: pytest.fail("no poll after loss"),
                             redeliver=lambda _ids: pytest.fail("no redelivery after loss"), renew=lambda: None,
                             stopped=lambda: False, log=lambda _message: None)
    execution = runner("import time; time.sleep(20)")
    with pytest.raises(CompanionSessionEnded):
        execution.call("answer", "synthetic", cooperate=pump.step)
    assert checks == [True] and execution._process.poll() is not None
