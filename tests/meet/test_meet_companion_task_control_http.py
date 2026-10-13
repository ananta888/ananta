"""Real loopback Hub HTTP controls abort an owned synthetic model process.

Task storage is a deterministic double; this is not SQL/dispatch acceptance.
"""

import sys
import threading
import time
from contextlib import contextmanager

import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

from tests.meet.test_meet_companion_task_grant_client import KEY, URL, assignment
from tests.meet.test_meet_companion_task_grant_routes import hub as hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionTaskControls
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient


@contextmanager
def running_hub(app):
    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    server = make_server("127.0.0.1", 0, app, request_handler=Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield URL.replace("127.0.0.1", "127.0.0.1:" + str(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("withdrawal", ["cancel", "pause"])
def test_signed_http_withdrawal_stops_owned_model_and_cannot_restore_old_operation(hub, withdrawal):
    app, fixture, _payload, _key = hub
    value = assignment() | {"session_id": "hub-session", "deadline": fixture.context["deadline"]}
    with running_hub(app) as endpoint:
        gate = CompanionTaskControls(AssignedCompanionGrantClient(value, endpoint, KEY))
        execution = CompanionExecutionProcess(command=(sys.executable, "-c",
            "import sys,time;sys.stdin.readline();time.sleep(30)"))
        try:
            bound = time.monotonic() + 3
            while gate.state is None:
                assert time.monotonic() < bound, "initial Hub observation timed out"
                gate.step()
                time.sleep(0.01)
            checkpoint = gate.checkpoint("chat")
            withdrawn = False

            def cooperate():
                nonlocal withdrawn
                if not withdrawn:
                    withdrawn = True
                    if withdrawal == "cancel":
                        fixture.task.status = "cancelled"
                    else:
                        fixture.context["controls"]["revision"] = 2
                        fixture.context["controls"]["chat"] = {
                            "enabled": False, "revision": 2, "since": int(time.time() * 1000),
                        }
                gate.step()
                gate.require_current(checkpoint)

            started = time.monotonic()
            with pytest.raises(CompanionControlLost):
                execution.call("answer", "synthetic question", cooperate=cooperate)
            assert time.monotonic() - started < 3
            assert execution._process.poll() is not None
            with pytest.raises(CompanionControlLost):
                gate.require_current(checkpoint)
        finally:
            execution.close()
            gate.close()
