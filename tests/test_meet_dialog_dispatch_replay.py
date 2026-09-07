"""Independent process/SQLite admission checks, not actual browser crash evidence."""

import multiprocessing
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.meet_dialog_executor_process import probe
from tests.test_meet_dialog_transport import assignment

pytestmark = pytest.mark.timeout(40)


@pytest.fixture(autouse=True)
def probe_import_root(monkeypatch):
    # Fresh spawn imports must select this checkout, not another dependency's
    # `tests` package that the broad application fixture added to sys.path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))


@contextmanager
def probes(path, value, modes):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    owned = []
    try:
        for mode in modes:
            reader, writer = context.Pipe(duplex=False)
            process = context.Process(target=probe, args=(str(path), value, mode, start, writer))
            process.start()
            writer.close()
            owned.append((process, reader))
        for process, reader in owned:
            assert reader.poll(10), "bounded probe readiness timeout"
            assert reader.recv() == {"phase": "ready"}
        start.set()
        yield owned
    finally:
        start.set()
        for process, reader in owned:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            assert not process.is_alive(), "owned probe cleanup failed"
            reader.close()
            process.close()


def result(owned):
    process, reader = owned
    assert reader.poll(10), "bounded probe result timeout"
    outcome = reader.recv()
    process.join(timeout=3)
    assert process.exitcode == 0
    assert outcome["phase"] == "result"
    return outcome["code"], outcome["launches"]


def persisted(path):
    with sqlite3.connect(path) as database:
        return database.execute("SELECT id, deadline FROM dialog_leases ORDER BY id").fetchall()


def test_two_processes_admit_exactly_one_dispatch_and_restart_keeps_fence(tmp_path):
    path = tmp_path / "dialog-leases.sqlite"
    value = assignment()
    with probes(path, value, ["normal", "normal"]) as owned:
        assert sorted(result(item) for item in owned) == [("accepted", 1), ("meet_dialog_replayed", 0)]
    assert persisted(path) == [(value["lease_id"], value["deadline"])]
    with probes(path, value, ["normal"]) as owned:
        assert result(owned[0]) == ("meet_dialog_replayed", 0)
    changed_identity = value | {
        "task_id": "foreign-task",
        "runtime_id": "foreign-runtime",
        "tenant_id": "foreign-tenant",
    }
    with probes(path, changed_identity, ["normal"]) as owned:
        assert result(owned[0]) == ("meet_dialog_replayed", 0)
    independent = value | {
        "lease_id": "independent-dispatch",
        "task_id": "independent-task",
        "runtime_id": "independent-runtime",
    }
    with probes(path, independent, ["normal"]) as owned:
        assert result(owned[0]) == ("accepted", 1)
    assert len(persisted(path)) == 2


def test_abrupt_exit_after_commit_does_not_release_uncertain_reservation(tmp_path):
    path = tmp_path / "dialog-leases.sqlite"
    value = assignment()
    with probes(path, value, ["crash-before-launch"]) as owned:
        process, reader = owned[0]
        process.join(timeout=10)
        assert process.exitcode == 23
        assert reader.poll(1)
        with pytest.raises(EOFError):
            reader.recv()
    assert persisted(path) == [(value["lease_id"], value["deadline"])]
    with probes(path, value, ["normal"]) as owned:
        assert result(owned[0]) == ("meet_dialog_replayed", 0)


def test_failed_launch_remains_fenced_after_process_restart(tmp_path):
    path = tmp_path / "dialog-leases.sqlite"
    value = assignment()
    with probes(path, value, ["failed-launch"]) as owned:
        assert result(owned[0]) == ("launch_failed", 0)
    assert persisted(path) == [(value["lease_id"], value["deadline"])]
    with probes(path, value, ["normal"]) as owned:
        assert result(owned[0]) == ("meet_dialog_replayed", 0)
