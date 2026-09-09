"""Private real-clock Hub process loss, persistent Tasks and independent Worker stop."""

import json
import os
import time
from contextlib import ExitStack

import pytest

from tests.meet_dialog_worker_container import DialogWorkerContainer
from tests.meet_receive_infrastructure import private_receive
from tests.meet_restart_hub_container import RestartHubContainer

pytestmark = [
    pytest.mark.timeout(360),
    pytest.mark.skipif(
        os.environ.get("MEET_HUB_RESTART_GATE") != "1", reason="explicit private packaged Hub restart gate"
    ),
]

PROCESS_COUNTS = r"""
import json, os
from pathlib import Path
runtime = browser = 0
paths = [p for p in Path('/proc').iterdir() if p.name.isdecimal()]
assert len(paths) <= 256
for path in paths:
    try:
        fields = (path / 'stat').read_text().rpartition(')')[2].split()
        with (path / 'cmdline').open('rb') as source: raw = source.read(4097)
    except FileNotFoundError:
        continue
    assert len(raw) <= 4096
    if fields[0] == 'Z': continue
    args = raw.rstrip(bytes([0])).split(bytes([0]))
    runtime += args[-2:] == [b'-m', b'worker.meet_media.dialog_runtime']
    browser += os.path.basename(args[0]) in {
        b'chrome', b'chrome-headless-shell', b'chrome_crashpad_handler', b'headless_shell', b'node'}
print(json.dumps({'runtime': runtime, 'browser': browser}))
"""


def counts(worker):
    raw = worker.command("exec", worker.name, "python", "-S", "-c", PROCESS_COUNTS)
    assert len(raw) < 100
    value = json.loads(raw)
    assert set(value) == {"runtime", "browser"}
    assert all(type(n) is int and 0 <= n < 256 for n in value.values())
    return value


def test_full_private_hub_restart_does_not_redispatch_or_extend_original_task(tmp_path, monkeypatch, record_property):
    monkeypatch.setenv("MEET_VISUAL_PACKAGED_GATE", "1")
    with private_receive(tmp_path, source="camera") as wire, ExitStack() as cleanup:
        hub = RestartHubContainer(wire, os.environ["MEET_HUB_TEST_IMAGE"], tmp_path)
        cleanup.callback(hub.close)
        hub.prepare()
        worker = DialogWorkerContainer(
            wire.ready["test_network"],
            os.environ["MEET_VISUAL_WORKER_IMAGE"],
            hub.origin + "/api/meet/v1/internal/dialog",
            lifetime=300,
            diagnostics=True,
        )
        cleanup.callback(worker.close)
        worker.start(wire.worker_key, wire.ready["certificate"], wire.spki, hub_identity=(hub.identifier, hub.image))
        hub.start(worker.origin)
        task_id = hub.request("POST", "/__test/start")["task_id"]
        status_path = "/__test/status/" + task_id
        original = hub.request("GET", status_path)
        wire.command("source")
        wire.command("grant")
        hub.request("POST", "/__test/enable/" + task_id)
        until = time.monotonic() + 25
        while time.monotonic() < until:
            before = hub.request("GET", status_path)
            assert before["status"] == "in_progress", "parent ended before Hub crash injection"
            if before["child_status"] == "completed":
                break
            time.sleep(0.1)
        assert before["child_status"] == "completed", "real visual reception did not complete before crash"
        assert wire.command("members") == {"participants": 2}
        running = counts(worker)
        assert running["runtime"] == 1 and running["browser"] >= 2
        began = time.monotonic()
        pid = hub.kill()
        until = began + 4
        while time.monotonic() < until:
            after = counts(worker)
            if after == {"runtime": 0, "browser": 0}:
                break
            time.sleep(0.05)
        stop_ms = (time.monotonic() - began) * 1000
        assert after == {"runtime": 0, "browser": 0} and stop_ms < 4000, "old Worker survived Hub loss budget"
        assert wire.command("members") == {"participants": 1}
        hub.restart(pid)
        restored = hub.request("GET", status_path)
        assert restored["deadline"] == original["deadline"] and restored["dialog_tasks"] == 1
        assert restored["status"] == "in_progress" and restored["child_status"] == "completed"
        assert restored["deadline_events"] == 0
        assert counts(worker) == {"runtime": 0, "browser": 0}
        assert wire.command("members") == {"participants": 1}
        remaining = original["deadline"] - time.time()
        assert 0 < remaining <= 120
        until = time.monotonic() + remaining + 8
        while time.monotonic() < until:
            terminal = hub.request("GET", status_path)
            if terminal["status"] == "failed":
                break
            time.sleep(0.2)
        assert time.time() >= original["deadline"]
        assert terminal["status"] == "failed" and terminal["deadline_events"] == 1
        assert terminal["child_status"] == "completed" and terminal["dialog_tasks"] == 1
        assert terminal["deadline"] == original["deadline"]
        assert hub.request("GET", status_path) == terminal
        record_property(
            "packaged_hub_restart",
            {
                "hub_process_replaced": True,
                "same_persisted_task": True,
                "worker_stop_ms": round(stop_ms, 2),
                "original_deadline_settled_once": True,
                "redispatch": False,
                "real_clock": True,
                "synthetic_policy": True,
                "production_release_evidence": False,
            },
        )
