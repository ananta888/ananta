"""Post-cleanup ordering and owned child deadlines without runner signal changes."""

import io
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_assignment_handoff import assignment

pytestmark = pytest.mark.timeout(10)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", [None, "control", "finish", "report"])
def test_main_reports_only_after_run_cleanup_and_finish_without_changing_outcome(monkeypatch, enabled, failure):
    from worker.meet_media import dialog_runtime as runtime

    events = []
    original = ValueError("meet_dialog_control_state_stale")
    source = io.BytesIO(json.dumps(assignment()).encode())
    hub = Mock(deadline=time.monotonic() + 60)

    def run(*_args):
        assert source.closed
        events.append("cleanup")
        if failure == "control":
            raise original

    def finish(action, **kwargs):
        events.append("finish")
        assert action == "finish" and kwargs == {"status": "failed" if failure == "control" else "completed"}
        if failure == "finish":
            raise OSError("private finish failure")

    def report(value):
        events.append("report")
        assert value["stop_reason"] == ("control_stale" if failure == "control" else "assignment_elapsed")
        if failure == "report":
            raise OSError("private report failure")
        return True

    hub.call.side_effect, hub.report_terminal.side_effect = finish, report
    monkeypatch.setattr(runtime.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(runtime, "HubDialogClient", lambda _assignment: hub)
    monkeypatch.setattr(runtime, "run", run)
    # Do not borrow the test runner's alarm. The actual guard runs in owned
    # process tests below; this test verifies composition and original failure.
    monkeypatch.setattr(runtime, "bounded_terminal_report", lambda send, _deadline: send())
    monkeypatch.setenv("MEET_DIALOG_DIAGNOSTICS_ENABLED", "1" if enabled else "0")
    if failure == "control":
        with pytest.raises(ValueError) as error:
            runtime.main()
        assert error.value is original
    else:
        runtime.main()
    assert events == ["cleanup", "finish"] + (["report"] if enabled else [])


@pytest.mark.parametrize("scenario", ["blocking", "cleanup-budget", "existing-timer", "thread", "success", "failure"])
def test_real_child_restores_signals_and_never_extends_original_cleanup_budget(scenario):
    code = """
import json, signal, threading, time
from worker.meet_media.dialog_diagnostics_deadline import bounded_terminal_report
scenario = __import__('sys').argv[1]
called = []
def handler(*_):
    raise AssertionError('an existing timer must never be consumed')
signal.signal(signal.SIGALRM, handler)
def send():
    called.append(True)
    if scenario in ('blocking', 'cleanup-budget'):
        time.sleep(10)
    if scenario == 'failure':
        raise OSError('private failure')
    return True
deadline = time.monotonic() + (0.2 - 5 if scenario == 'cleanup-budget' else 60)
if scenario == 'existing-timer':
    signal.setitimer(signal.ITIMER_REAL, 10)
started = time.monotonic()
result = []
if scenario == 'thread':
    thread = threading.Thread(target=lambda: result.append(bounded_terminal_report(send, deadline)))
    thread.start(); thread.join(timeout=2)
    assert not thread.is_alive()
else:
    result.append(bounded_terminal_report(send, deadline))
elapsed = time.monotonic() - started
assert signal.getsignal(signal.SIGALRM) is handler
timer = signal.getitimer(signal.ITIMER_REAL)
assert (timer[0] > 5 if scenario == 'existing-timer' else timer == (0, 0))
signal.setitimer(signal.ITIMER_REAL, 0)
assert result == [scenario == 'success']
assert bool(called) == (scenario not in ('thread', 'existing-timer'))
assert elapsed < (0.8 if scenario == 'cleanup-budget' else 1.7)
print(json.dumps({'elapsed': elapsed, 'accepted': result[0]}))
"""
    child = subprocess.run([sys.executable, "-c", code, scenario], capture_output=True, timeout=4, check=True)
    result = json.loads(child.stdout)
    if scenario == "blocking":
        assert 0.8 < result["elapsed"] < 1.7
    elif scenario == "cleanup-budget":
        assert 0.1 < result["elapsed"] < 0.8
