"""Process-owned probe: real replay DB, explicitly synthetic launch/watch ports."""

import io
import os


class _CompletedProcess:
    def __init__(self):
        self.stdin = io.BytesIO()

    def wait(self, *, timeout):
        return 0


class _InlineWatch:
    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def probe(path, assignment, mode, start, connection):
    """All replacements live in this test-owned spawned process, never the Hub."""
    from worker.meet_media import dialog_executor

    launches = 0

    def launch(*args, **kwargs):
        nonlocal launches
        if mode == "crash-before-launch":
            os._exit(23)  # Intentional process loss, after the real DB commit.
        if mode == "failed-launch":
            raise OSError("synthetic-launch-failure")
        launches += 1
        return _CompletedProcess()

    try:
        executor = dialog_executor.DialogExecutor(path, slots=1)
        dialog_executor.subprocess.Popen = launch
        dialog_executor.threading.Thread = _InlineWatch
        connection.send({"phase": "ready"})
        if not start.wait(5):
            connection.send({"phase": "error", "code": "probe_start_timeout"})
            return
        try:
            accepted = executor.start(assignment)
        except ValueError as error:
            code = str(error)
            connection.send(
                {
                    "phase": "result",
                    "code": code
                    if code
                    in {
                        "meet_dialog_replayed",
                        "meet_dialog_worker_busy",
                        "meet_dialog_deadline_invalid",
                    }
                    else "unexpected_rejection",
                    "launches": launches,
                }
            )
        except OSError:
            connection.send({"phase": "result", "code": "launch_failed", "launches": launches})
        else:
            connection.send({"phase": "result", "code": accepted["status"], "launches": launches})
    finally:
        connection.close()
