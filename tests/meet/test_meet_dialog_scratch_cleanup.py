"""Outer Executor owns scratch even when its Task runtime cannot finalize."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.meet.test_meet_dialog_assignment_handoff import assignment
from worker.meet_media import dialog_executor


@pytest.mark.timeout(12)
def test_forced_runtime_stop_removes_scratch_before_releasing_execution_slot(tmp_path, monkeypatch):
    marker = tmp_path / "owned-scratch.json"
    code = f'''
import json,os,time
from pathlib import Path
root=Path(os.environ["ANANTA_MEET_TASK_SCRATCH_DIR"])
(root/"orphan.wav").write_bytes(b"synthetic-private-audio")
Path({str(marker)!r}).write_text(json.dumps(str(root)))
time.sleep(30)
'''
    launch = subprocess.Popen
    children = []
    def spawn(_command, **options):
        child = launch([sys.executable, "-c", code], **options)
        children.append(child)
        return child
    def force_stop(process, progress, budget, stop):
        deadline = time.monotonic() + 3
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        stop(process)
    monkeypatch.setattr(dialog_executor.subprocess, "Popen", spawn)
    monkeypatch.setattr(dialog_executor, "watch_dialog_progress", force_stop)
    executor = dialog_executor.DialogExecutor(tmp_path / "replay.sqlite", slots=1)
    try:
        assert executor.start(assignment())["status"] == "accepted"
        deadline = time.monotonic() + 4
        while not executor.slots.acquire(blocking=False):
            assert time.monotonic() < deadline
            time.sleep(.02)
        try:
            scratch = Path(json.loads(marker.read_text()))
            assert not scratch.exists() and not scratch.parent.exists()
            assert children[0].poll() is not None
        finally:
            executor.slots.release()
    finally:
        for child in children:
            dialog_executor.DialogExecutor._stop(child)
