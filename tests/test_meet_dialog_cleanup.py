"""No business assertion is retried; only bounded fixture resource disposal."""

import sqlite3
from contextlib import closing, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from tests.meet_dialog_cleanup import cancel_fixture_dialog, close_dialog_servers


def locked():
    original = sqlite3.OperationalError("synthetic shared-cache table lock")
    original.sqlite_errorcode = sqlite3.SQLITE_LOCKED | 256
    return OperationalError("synthetic terminal CAS", {}, original)


def test_only_bounded_sqlite_contention_retries_the_same_authorized_terminal_cas():
    app = SimpleNamespace(app_context=nullcontext)
    service = Mock()
    service.inspect.side_effect = [locked(), locked(), {"status": "cancelled"}]
    pause = Mock()
    cancel_fixture_dialog(app, service, "principal", "owned-task", clock=lambda: 0, pause=pause)
    assert service.inspect.call_count == 3
    for call in service.inspect.call_args_list:
        assert call.args == ("principal", "synthetic", "owned-task") and call.kwargs == {"stop": True}
    assert pause.call_count == 2


@pytest.mark.parametrize(
    "error",
    [
        ValueError("policy_denied"),
        OperationalError("sql", {}, Exception("not SQLite")),
        OperationalError("sql", {}, sqlite3.OperationalError("other failure")),
    ],
)
def test_other_errors_never_trigger_an_implicit_retry(error):
    app, service, pause = SimpleNamespace(app_context=nullcontext), Mock(), Mock()
    service.inspect.side_effect = error
    with pytest.raises(type(error)):
        cancel_fixture_dialog(app, service, "principal", "owned-task", pause=pause)
    service.inspect.assert_called_once()
    pause.assert_not_called()


def test_expired_cleanup_budget_and_attempt_limit_remain_failures():
    app, service, pause = SimpleNamespace(app_context=nullcontext), Mock(), Mock()
    service.inspect.side_effect = locked()
    with pytest.raises(OperationalError):
        cancel_fixture_dialog(app, service, "principal", "owned-task", clock=Mock(side_effect=[0, 2]), pause=pause)
    service.inspect.assert_called_once()
    pause.assert_not_called()
    service.inspect.reset_mock()
    with pytest.raises(OperationalError):
        cancel_fixture_dialog(app, service, "principal", "owned-task", clock=lambda: 0, pause=pause)
    assert service.inspect.call_count == 3


def test_failed_cancel_still_joins_and_closes_every_owned_test_server():
    app, service, thread, hub, worker = SimpleNamespace(app_context=nullcontext), Mock(), Mock(), Mock(), Mock()
    service.inspect.side_effect = ValueError("synthetic cancel failure")
    with pytest.raises(ValueError, match="cancel failure"):
        close_dialog_servers(app, service, "principal", {"task_id": "owned-task"}, thread, (hub, worker))
    thread.join.assert_called_once_with(timeout=10)
    for server in (hub, worker):
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
    # Partial setup owns no task or server and must not infer one.
    close_dialog_servers(app, None, "principal", None, None, (None, None))


def test_real_shared_cache_sqlite_read_lock_is_released_before_terminal_retry():
    uri = "file:avatar-cleanup-" + uuid4().hex + "?mode=memory&cache=shared"
    calls = []
    with closing(sqlite3.connect(uri, uri=True)) as reader:
        reader.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        reader.execute("INSERT INTO tasks VALUES ('owned-task', 'in_progress')")
        reader.commit()
        reader.execute("BEGIN")
        assert reader.execute("SELECT status FROM tasks").fetchone() == ("in_progress",)

        def inspect(principal, project, task_id, *, stop):
            assert (principal, project, task_id, stop) == ("principal", "synthetic", "owned-task", True)
            calls.append(task_id)
            try:
                with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as writer, writer:
                    writer.execute(
                        "UPDATE tasks SET status='cancelled' WHERE id=? AND status='in_progress'", (task_id,)
                    )
            except sqlite3.OperationalError as error:
                raise OperationalError("synthetic terminal CAS", {}, error) from error

        pauses = []

        def release_reader(delay):
            pauses.append(delay)
            reader.rollback()

        cancel_fixture_dialog(
            SimpleNamespace(app_context=nullcontext),
            SimpleNamespace(inspect=inspect),
            "principal",
            "owned-task",
            pause=release_reader,
        )
        assert calls == ["owned-task", "owned-task"] and pauses == [0.05]
        assert reader.execute("SELECT status FROM tasks").fetchone() == ("cancelled",)
