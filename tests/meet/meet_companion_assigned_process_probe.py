"""Test-owned run adapter; real entrypoint, signed Hub reads and parent progress.

This probe deliberately has no browser and cannot prove browser acceptance.
"""

import sys
import time
from pathlib import Path

from worker.meet_media import companion_task_runtime
from worker.meet_media.companion_task_controls import CompanionTaskControls


def run(client, *, log, progress, hub=None):
    assert progress is not None
    controls = CompanionTaskControls(client)
    try:
        while True:
            observed = controls.step()
            if observed is not None:
                progress.report(controls.fresh_until, client.deadline)
                if sys.argv[1].startswith('observe-'):
                    hub.call('observe', meet_session_id='ms_' + 'a' * 32)
                Path(sys.argv[2]).touch()
                if sys.argv[1] in {"complete", "observe-complete"}:
                    return
                if sys.argv[1] == "stall":
                    time.sleep(30)  # Independent parent must stop this owned process.
            time.sleep(0.01)
    finally:
        controls.close()


companion_task_runtime.run = run
try:
    companion_task_runtime.main()
except BaseException:
    sys.exit(1)
