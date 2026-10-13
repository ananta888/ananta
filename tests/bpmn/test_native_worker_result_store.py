"""Synthetic Worker result persistence and signed retrieval; no release evidence."""

import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from tests.bpmn.test_native_task_execution_http import TOKEN, WORKER, signed  # noqa: F401
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_store import MAX_RESULT_BYTES, SQLiteNativeResultStore

# ruff: noqa: F811 -- named imported pytest fixture.


@pytest.fixture
def store(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return SQLiteNativeResultStore(directory / "results.sqlite3")


def test_result_survives_reopening_and_never_reexecutes(store, signed):
    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "synthetic"})
    reopened = SQLiteNativeResultStore(store.path)
    assert reopened.read(task)["output"] == "synthetic"
    with pytest.raises(ValueError, match="already_reserved"):
        reopened.reserve(task)


@pytest.mark.parametrize("field,value", [("project_id", "foreign"), ("id", "foreign"),
                                         ("tenant_id", "foreign"), ("input_files", ["different"])])
def test_foreign_projection_cannot_read_result(store, signed, field, value):
    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed"})
    other = copy.deepcopy(task)
    other[field] = value
    with pytest.raises(ValueError):
        store.read(other)


def test_expiry_scrubs_payload_but_keeps_no_resend_tombstone(store, signed):
    clock = [100]
    store = SQLiteNativeResultStore(store.path, clock=lambda: clock[0], retention_seconds=1)
    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "private"})
    clock[0] = 101
    with pytest.raises(ValueError, match="expired"):
        store.read(task)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state, result_json, result_digest FROM native_results").fetchone() == (
            "expired", None, None)
    with pytest.raises(ValueError, match="already_reserved"):
        store.reserve(task)


def test_lost_execution_result_and_corruption_fail_closed(store, signed):
    task = signed[0]["task"]
    store.reserve(task)
    with pytest.raises(ValueError, match="not_ready"):
        SQLiteNativeResultStore(store.path).read(task)
    store.publish(task, {"status": "completed"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET result_json='{}'")
    with pytest.raises(ValueError, match="corrupt"):
        store.read(task)


def test_concurrent_worker_requests_reserve_once(store, signed):
    task = signed[0]["task"]

    def reserve(_):
        try:
            SQLiteNativeResultStore(store.path).reserve(task)
            return "execute"
        except ValueError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == ["denied", "execute"]


def test_result_retrieval_does_not_call_consumer_and_requires_signature(store, signed):
    value, verifier = signed
    calls = []
    app = Flask(__name__)
    app.config["ROLE"] = "worker"
    app.register_blueprint(create_native_execution_blueprint(
        token_provider=lambda: TOKEN, recipient_provider=lambda: (WORKER["worker_id"], WORKER["worker_url"]),
        verification_provider=lambda: verifier, result_store_provider=lambda: store,
        consumer=lambda task: calls.append(task) or {"status": "completed", "output": "synthetic"},
    ))
    client = app.test_client()
    headers = {"Authorization": "Bearer " + TOKEN}
    assert client.post("/api/internal/native-tasks/execute", json=value, headers=headers).status_code == 200
    assert client.post("/api/internal/native-tasks/result", json=value, headers=headers).json["output"] == "synthetic"
    assert len(calls) == 1
    assert client.post("/api/internal/native-tasks/execute", json=value, headers=headers).status_code == 409
    changed = copy.deepcopy(value)
    changed["task"]["project_id"] = "foreign"
    assert client.post("/api/internal/native-tasks/result", json=changed, headers=headers).status_code == 422
    assert client.post("/api/internal/native-tasks/result", json=value).status_code == 401
    assert len(calls) == 1


def test_store_rejects_public_path_and_oversized_result(store, signed, tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private_directory"):
        SQLiteNativeResultStore(public / "results.sqlite3")
    task = signed[0]["task"]
    store.reserve(task)
    with pytest.raises(ValueError, match="outcome_limit"):
        store.publish(task, {"status": "completed", "output": "x" * MAX_RESULT_BYTES})


def test_expiry_maintenance_is_bounded_and_preserves_replay_fences(store, signed):
    clock = [100]
    store = SQLiteNativeResultStore(store.path, clock=lambda: clock[0], retention_seconds=1)
    task = signed[0]["task"]
    # Minimal deterministic slots exercise batch handling without content reads.
    with sqlite3.connect(store.path) as db:
        for index in range(3):
            db.execute("INSERT INTO native_results VALUES (?, ?, ?, 'ready', ?, ?, ?)",
                       (task["tenant_id"], "synthetic-slot-" + str(index), "bound", '"private"', "digest", 101))
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "private"})
    clock[0] = 101
    assert store.expire_results(batch_size=2) == 2
    assert store.expire_results(batch_size=2) == 2
    assert store.expire_results(batch_size=2) == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM native_results WHERE state='expired' "
                          "AND result_json IS NULL AND result_digest IS NULL").fetchone()[0] == 4
    with pytest.raises(ValueError, match="already_reserved"):
        store.reserve(task)


def test_reopening_scrubs_unread_expired_payload_and_publication_stays_denied(store, signed):
    clock = [100]
    original = SQLiteNativeResultStore(store.path, clock=lambda: clock[0], retention_seconds=1)
    task = signed[0]["task"]
    original.reserve(task)
    original.publish(task, {"status": "completed", "output": "private"})
    clock[0] = 101
    reopened = SQLiteNativeResultStore(store.path, clock=lambda: clock[0])
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state, result_json FROM native_results").fetchone() == ("expired", None)
    with pytest.raises(ValueError, match="publication_denied"):
        reopened.publish(task, {"status": "completed"})


@pytest.mark.parametrize("size", [0, -1, 1001, True, 1.5, "100"])
def test_expiry_rejects_unbounded_batch_sizes(store, size):
    with pytest.raises(ValueError, match="expiry_batch_invalid"):
        store.expire_results(batch_size=size)


def test_finite_maintenance_cli_reports_no_content_and_preserves_tombstone(store, signed, monkeypatch, capsys):
    import json

    from worker.runtime.native_graph.result_maintenance import main

    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "synthetic-secret-canary"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET expires_at=0")
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_STORE_PATH", str(store.path))
    assert main(["--batch-size", "1"]) == 0
    report = capsys.readouterr().out
    assert "synthetic-secret-canary" not in report and str(store.path) not in report
    assert json.loads(report)["scrubbed_results"] == 1
    with pytest.raises(ValueError, match="already_reserved"):
        store.reserve(task)
    assert main(["--batch-size", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["scrubbed_results"] == 0
    monkeypatch.delenv("ANANTA_NATIVE_RESULT_STORE_PATH")
    assert main([]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


def test_expiry_on_unrelated_request_scrubs_unread_old_payload(store, signed):
    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "private"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET expires_at=0")
    other = {**task, "id": "synthetic-new-task"}
    store.reserve(other)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT result_json FROM native_results WHERE task_id=?", (task["id"],)).fetchone() == (None,)


def test_retention_does_not_free_quota_by_removing_replay_tombstones(store, signed):
    limited = SQLiteNativeResultStore(store.path, max_records=1)
    task = signed[0]["task"]
    limited.reserve(task)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET expires_at=0")
    assert limited.expire_results() == 1
    with pytest.raises(ValueError, match="store_full"):
        limited.reserve({**task, "id": "synthetic-new-task"})


def test_cleanup_expires_unknown_execution_without_touching_live_results(store, signed):
    task = signed[0]["task"]
    other = {**task, "id": "synthetic-live-task"}
    store.reserve(task)
    store.reserve(other)
    store.publish(other, {"status": "completed", "output": "live"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET expires_at=0 WHERE task_id=?", (task["id"],))
    assert store.expire_results() == 1
    assert store.read(other)["output"] == "live"
    with pytest.raises(ValueError, match="publication_denied"):
        store.publish(task, {"status": "completed"})
    with pytest.raises(ValueError, match="already_reserved"):
        store.reserve(task)


def test_invalid_cli_batch_does_not_create_store(tmp_path, monkeypatch, capsys):
    from worker.runtime.native_graph.result_maintenance import main

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "must-not-be-created.sqlite3"
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_STORE_PATH", str(path))
    assert main(["--batch-size", "1001"]) == 1
    assert not path.exists() and "blocked" in capsys.readouterr().out


def test_maintenance_runs_as_bounded_headless_subprocess(store, signed):
    import json
    import subprocess
    import sys
    from pathlib import Path

    task = signed[0]["task"]
    store.reserve(task)
    store.publish(task, {"status": "completed", "output": "private-canary"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_results SET expires_at=0")
    result = subprocess.run(
        [sys.executable, "-m", "worker.runtime.native_graph.result_maintenance", "--batch-size", "1"],
        cwd=Path(__file__).resolve().parents[2],
        env={"ANANTA_NATIVE_RESULT_STORE_PATH": str(store.path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert json.loads(result.stdout)["scrubbed_results"] == 1
    assert "private-canary" not in result.stdout and result.stderr == ""
