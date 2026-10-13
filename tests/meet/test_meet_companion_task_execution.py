"""Actual model pipe under assigned source checkpoints; no provider calls."""

import sys
from unittest.mock import Mock

import pytest

from tests.meet.test_meet_companion_task_controls import fixture
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted
from worker.meet_media.companion_task_execution import AssignedCompanionExecution


@pytest.mark.timeout(10)
@pytest.mark.parametrize("mode", ["normal", "speech_paused", "chat_paused", "send_failed"])
def test_assigned_round_never_replies_after_chat_pause_or_speaks_after_speech_pause(mode):
    now, controls, state, _pool = fixture()
    process = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", """
import base64,json,sys,time
for line in sys.stdin:
    value=json.loads(line);time.sleep(.05)
    result='synthetic answer' if value['kind']=='answer' else base64.b64encode(b'\\x00\\x01'*50).decode()
    print(json.dumps({'kind':value['kind'],'result':result}),flush=True)
"""))
    sent, spoken = Mock(return_value=mode != "send_failed"), Mock()
    calls = [0]

    def cooperate():
        calls[0] += 1
        if mode == "chat_paused" and calls[0] == 3:
            now[0] += 1
            controls.step()
            state["controls"]["revision"] = 2
            state["controls"]["chat"] = {"enabled": False, "revision": 2, "since": 2000}
            controls.exchange.pending.set_result(state)
            controls.step()

    if mode == "speech_paused":
        controls.state["controls"]["speech"]["enabled"] = False
    runtime = AssignedCompanionExecution(controls, process, cooperate=cooperate, publish_speech=spoken)
    try:
        if mode == "chat_paused":
            with pytest.raises(CompanionSourceInterrupted):
                runtime.answer({"text": "synthetic question"}, sent)
            sent.assert_not_called()
            assert process._process.poll() is not None
        else:
            runtime.answer({"text": "synthetic question"}, sent)
            sent.assert_called_once_with("synthetic answer")
        assert spoken.call_count == (1 if mode == "normal" else 0)
    finally:
        process.close()
        controls.close()
