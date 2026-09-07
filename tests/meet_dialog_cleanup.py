"""Bounded cleanup for this fixture's own Hub task and threaded test servers."""

import sqlite3
import time
from contextlib import ExitStack

from sqlalchemy.exc import OperationalError


def cancel_fixture_dialog(app, service, principal, task_id, *, clock=time.monotonic, pause=time.sleep):
    deadline = clock() + 1
    for attempt in range(3):
        try:
            # Each attempt rereads the real Hub task and uses its terminal CAS.
            # Never retry a start, control mutation, auth denial or arbitrary SQL error.
            with app.app_context():
                service.inspect(principal, "synthetic", task_id, stop=True)
            return
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", 0)
            if (
                not isinstance(error.orig, sqlite3.OperationalError)
                or code & 255 not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                or attempt == 2
                or clock() + 0.05 >= deadline
            ):
                raise
            pause(0.05)


def close_dialog_servers(app, service, principal, started, runtime_thread, servers):
    # Even a failed final CAS must reap the server resources. The old linear
    # teardown skipped thread/server cleanup when task cancellation raised.
    with ExitStack() as cleanup:
        for server in servers:
            if server is not None:
                cleanup.callback(server.server_close)
                cleanup.callback(server.shutdown)
        if runtime_thread is not None:
            cleanup.callback(runtime_thread.join, timeout=10)
        if service is not None and started is not None:
            cancel_fixture_dialog(app, service, principal, started["task_id"])
