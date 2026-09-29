"""Owned real process groups prove stalls cannot block the independent watchdog."""

import os
import signal
import subprocess
import sys
import time

import pytest

from worker.meet_media.dialog_executor import DialogExecutor
from worker.meet_media.dialog_progress_budget import DialogProgressBudget
from worker.meet_media.dialog_progress_channel import DialogProgressChannel
from worker.meet_media.dialog_progress_watch import watch_dialog_progress

pytestmark = pytest.mark.timeout(20)

SCRIPT = """
import os, subprocess, sys, time
from worker.meet_media.dialog_progress_channel import inherited_progress, PROGRESS_FD
mode = sys.argv[1]
with inherited_progress() as progress:
    assert progress is not None and PROGRESS_FD not in os.environ
    fd = progress.channel.fileno()
    assert not os.get_inheritable(fd)
    if mode == 'malformed':
        progress.channel.send(b'private-malformed')
    elif mode != 'silent':
        if mode == 'descendant':
            check = 'import os,sys; fd=int(sys.argv[1]); '\
                '\\ntry: os.fstat(fd)\\nexcept OSError: print("closed")\\nelse: raise SystemExit(9)'
            result = subprocess.run([sys.executable, '-c', check, str(fd)], close_fds=False,
                                    capture_output=True, timeout=2)
            assert result.returncode == 0 and result.stdout == b'closed\\n'
        progress.report(time.monotonic() + 0.2, time.monotonic() + 2)
    if mode == 'complete':
        print('completed', flush=True)
    else:
        print('ready', flush=True)
        time.sleep(30)
"""


@pytest.mark.parametrize("mode", ["stalled", "silent", "malformed", "descendant", "complete"])
def test_real_stalled_child_is_killed_within_fixed_resource_bound_and_cannot_keep_descriptor(mode):
    channel, child = DialogProgressChannel(), None
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", SCRIPT, mode],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            **channel.child_options(),
        )
        channel.spawned()
        now = time.monotonic()
        budget = DialogProgressBudget(now + (0.7 if mode == "silent" else 3), now)
        watch_dialog_progress(child, channel, budget, DialogExecutor._stop)
        elapsed = time.monotonic() - now
        output, errors = child.communicate(timeout=2)
        if mode == "complete":
            assert child.returncode == 0 and output == b"completed\n", errors
        else:
            assert child.returncode == -signal.SIGKILL and output == b"ready\n", errors
            assert elapsed < 2, "child progress stall escaped resource-stop budget"
        assert not errors
        with pytest.raises(ProcessLookupError):
            os.getpgid(child.pid)
    finally:
        channel.close()
        if child is not None:
            if child.poll() is None:
                DialogExecutor._stop(child)
            child.wait(timeout=2)
            child.stdout.close()
            child.stderr.close()
