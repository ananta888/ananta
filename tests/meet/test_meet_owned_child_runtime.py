"""Actual process death and spawning-thread exit preserve helper ownership."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from worker.meet_media.companion_process_transport import CompanionProcessTransport


def running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in {"Z", "X"}
    except FileNotFoundError:
        return False


@pytest.mark.timeout(12)
def test_helper_is_killed_when_its_actual_parent_process_is_killed(tmp_path):
    marker = tmp_path / "ready.json"
    child = (f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); "
             "time.sleep(30)")
    code = f'''
import sys
from worker.meet_media.companion_process_transport import CompanionProcessTransport
transport=CompanionProcessTransport((sys.executable,"-c",{child!r}))
transport.exchange({{"synthetic":True}},cooperate=lambda:None)
'''
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 4
        while not marker.exists():
            assert parent.poll() is None and time.monotonic() < deadline
            time.sleep(.02)
        pid = int(marker.read_text())
        assert running(pid)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=2)
        deadline = time.monotonic() + 3
        while running(pid):
            assert time.monotonic() < deadline, "owned helper survived parent death"
            time.sleep(.02)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=2)


@pytest.mark.timeout(12)
def test_spawning_thread_exit_does_not_kill_a_helper_owned_by_the_live_process():
    code = "import json,sys; n=0\nfor line in sys.stdin:\n n+=1; print(json.dumps({'seen':n}),flush=True)"
    transport = CompanionProcessTransport((sys.executable, "-u", "-c", code))
    result = []
    thread = threading.Thread(target=lambda: result.append(transport.exchange({}, cooperate=lambda: None)))
    try:
        thread.start()
        thread.join(timeout=4)
        assert not thread.is_alive() and result == [{"seen": 1}]
        time.sleep(.2)
        assert transport.process.poll() is None
        assert transport.exchange({}, cooperate=lambda: None) == {"seen": 2}
    finally:
        transport.close()
