"""Bounded stdio/cleanup contracts before provisioning the opt-in live gate."""

import io
import json
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_silent_room_driver import SilentRoomDriver

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def pipe_driver():
    reader, writer = os.pipe()
    driver = object.__new__(SilentRoomDriver)
    driver.process = SimpleNamespace(stdout=os.fdopen(reader, "rb"), stdin=io.BytesIO())
    driver.pending, driver.closed = b"", False
    try:
        yield driver, writer
    finally:
        driver.process.stdout.close()
        os.close(writer)


def test_reads_bounded_lines_without_losing_buffered_next_reply(pipe_driver):
    driver, writer = pipe_driver
    os.write(writer, b'{"first":true}\n{"second":true}\n')
    assert driver.receive(1) == {"first": True}
    assert driver.receive(1) == {"second": True}


def test_incomplete_line_has_a_real_read_deadline(pipe_driver):
    driver, writer = pipe_driver
    os.write(writer, b'{"unfinished":')
    with pytest.raises(ValueError, match="read_timeout"):
        driver.receive(0.05)


@pytest.mark.parametrize("raw", [b"x" * 2049, b"[]\n", b'{"bridge_error":"private","stage":"secret/path"}\n'])
def test_oversize_or_failed_reply_cannot_be_an_observation(pipe_driver, raw):
    driver, writer = pipe_driver
    os.write(writer, raw)
    with pytest.raises(ValueError, match="silent_bridge_") as error:
        driver.receive(1)
    assert "secret/path" not in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("line", ["", "stop", "inspect\njoin-human", "bind room-ABC", "eval anything", "x" * 1000])
def test_python_command_port_is_closed_before_writing(pipe_driver, line):
    driver, _ = pipe_driver
    with pytest.raises(ValueError, match="command_invalid"):
        driver.command(line)
    assert driver.process.stdin.getvalue() == b""


def test_driver_cleanup_escalates_only_its_owned_process_and_is_idempotent(monkeypatch):
    process = Mock(pid=123456)
    process.wait.side_effect = [subprocess.TimeoutExpired("test", 35), subprocess.TimeoutExpired("test", 5), 0]
    driver = object.__new__(SilentRoomDriver)
    driver.process, driver.closed = process, False
    kill = Mock()
    monkeypatch.setattr("tests.meet_silent_room_driver.os.killpg", kill)
    driver.close()
    driver.close()
    assert [c.args for c in kill.call_args_list] == [(123456, signal.SIGTERM), (123456, signal.SIGKILL)]
    assert [c.kwargs for c in process.wait.call_args_list] == [{"timeout": 35}, {"timeout": 5}, {"timeout": 5}]
    process.stdin.close.assert_called_once()
    process.stdout.close.assert_called_once()


def test_partial_hub_composition_releases_all_registered_resources(monkeypatch):
    from tests.meet_silent_room_hub import SilentRoomHub

    released = []

    def fail(self, *_args):
        self.cleanup.callback(released.append, "worker")
        self.cleanup.callback(released.append, "hub")
        raise ValueError("synthetic-composition-failure")

    monkeypatch.setattr(SilentRoomHub, "_compose", fail)
    with pytest.raises(ValueError, match="composition-failure"):
        SilentRoomHub(Mock(), "https://example.test", None, None, monkeypatch)
    assert released == ["hub", "worker"]


def test_cleanup_error_cannot_be_reported_as_a_successful_gate():
    driver = object.__new__(SilentRoomDriver)
    driver.process, driver.closed = Mock(), False
    driver.process.wait.return_value = 1
    with pytest.raises(ValueError, match="cleanup_failed"):
        driver.close()
    driver.process.stdout.close.assert_called_once()


def test_worker_fixture_constructor_failure_still_finishes_bounded(monkeypatch):
    from tests.meet_silent_room_hub import _Execution

    monkeypatch.setattr("tests.meet_silent_room_hub.HubDialogClient", Mock(side_effect=ValueError("private-path")))
    execution = _Execution()
    execution.start({"task_id": "synthetic-task", "lease_id": "synthetic-lease", "runtime_id": "synthetic-runtime"})
    assert execution.finished.wait(2)
    execution.thread.join(timeout=2)
    assert not execution.thread.is_alive()
    assert execution.failures == ["ValueError"]


def test_exact_node_driver_parser_rejects_authority_and_import_is_side_effect_free():
    uri = Path(__file__).with_name("meet_silent_room_bridge.mjs").as_uri()
    script = f"""import assert from 'node:assert/strict';
      import {{command}} from {json.dumps(uri)};
      assert.deepEqual(command('inspect'), {{action:'inspect'}});
      assert.deepEqual(command('join-human'), {{action:'join-human'}});
      assert.deepEqual(command('stop'), {{action:'stop'}});
      assert.deepEqual(command('bind room-0123456789abcdef01'), {{action:'bind',room:'room-0123456789abcdef01'}});
      for (const value of ['', null, {{}}, 'eval arbitrary', 'inspect\\nstop',
        'bind room-ABC', 'bind https://public.test', 'x'.repeat(129)]) {{
        assert.throws(() => command(value), /silent_command_invalid/);
      }}
      process.stdout.write('closed-driver-ok');"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        env=os.environ | {"MEET_SILENT_ROOM_GATE": "1"},
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert result.stdout == "closed-driver-ok" and result.stderr == ""
