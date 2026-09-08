"""Owned real child processes exercise assignment input, never a browser or Hub."""

import hashlib
import io
import os
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media import dialog_executor
from worker.meet_media.assignment_input import dialog_assignment_input
from worker.meet_media.contract import encode

pytestmark = pytest.mark.timeout(12)


def assignment():
    return {
        "schema": "ananta.meet-dialog-assignment.v1",
        "task_id": "test-task",
        "lease_id": "test-lease",
        "runtime_id": "test-runtime",
        "session_id": "test-session",
        "tenant_id": "test-tenant",
        "project_id": "test-project",
        "deadline": int(time.time()) + 60,
        "capabilities": ["chat.read", "chat.send"],
        "audio_mode": "off",
        "meeting": {
            "origin": "https://meet.test.invalid",
            "room_id": "room-" + "a" * 18,
            "grant": "synthetic-" + "a" * (4096 - 10),
        },
    }


def test_nonreading_child_cannot_block_dispatch_before_watchdog_installation(tmp_path, monkeypatch):
    real_launch = subprocess.Popen
    owned = []

    def launch(command, **kwargs):
        assert command == [sys.executable, "-m", "worker.meet_media.dialog_runtime"]
        # 4 KiB is a legal Linux pipe capacity. The startup adapter must not
        # depend on pipe capacity or on an imported child reaching its read.
        if kwargs.get("stdin") == subprocess.PIPE:
            kwargs["pipesize"] = 4096
        child = real_launch([sys.executable, "-c", "import time; time.sleep(3)"], **kwargs)
        owned.append(child)
        return child

    executor = dialog_executor.DialogExecutor(tmp_path / "replay.sqlite", slots=1)
    monkeypatch.setattr(dialog_executor.subprocess, "Popen", launch)
    started = time.monotonic()
    try:
        result = executor.start(assignment())
        assert result["status"] == "accepted"
        assert time.monotonic() - started < 1.5
        with pytest.raises(ValueError, match="meet_dialog_worker_busy"):
            executor.start(assignment() | {"lease_id": "other-lease"})
    finally:
        for child in owned:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)


def test_real_child_receives_exact_anonymous_input_after_parent_descriptor_closes():
    raw = encode(assignment())
    child = None
    try:
        with dialog_assignment_input(raw) as source:
            metadata = os.fstat(source.fileno())
            assert stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 0
            assert stat.S_IMODE(metadata.st_mode) == 0o600
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
                ],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        assert source.closed and child.stdin is None
        output, _ = child.communicate(timeout=2)
        assert child.returncode == 0
        assert output == hashlib.sha256(raw).hexdigest().encode() + b"\n"
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            child.stdout.close()


@pytest.mark.parametrize("raw", [None, "private-text", b"", b"x" * 16385])
def test_invalid_payload_never_creates_an_input_file(raw):
    factory = Mock(side_effect=AssertionError("file must not be created"))
    with pytest.raises(ValueError, match="meet_dialog_payload_invalid"):
        with dialog_assignment_input(raw, file_factory=factory):
            pytest.fail("invalid input admitted")
    factory.assert_not_called()


@pytest.mark.parametrize("failure", ["short-write", "write", "seek", "consumer"])
def test_handoff_errors_close_the_descriptor_and_never_yield_partial_bytes(failure):
    class FaultyFile(io.BytesIO):
        def write(self, raw):
            if failure == "write":
                raise OSError("synthetic write fault")
            if failure == "short-write":
                return super().write(raw[:-1])
            return super().write(raw)

        def seek(self, offset):
            if failure == "seek":
                raise OSError("synthetic seek fault")
            return super().seek(offset)

    source = FaultyFile()
    factory = Mock(return_value=source)
    expected = ValueError if failure == "short-write" else OSError
    with pytest.raises(expected):
        with dialog_assignment_input(b"synthetic", file_factory=factory):
            assert failure == "consumer"
            raise OSError("synthetic consumer fault")
    assert source.closed
    factory.assert_called_once_with(mode="w+b", buffering=0)


def test_input_close_failure_after_spawn_still_kills_owned_child_and_keeps_replay_fence(tmp_path, monkeypatch):
    sources = []

    @contextmanager
    def closing_failure(raw):
        with dialog_assignment_input(raw) as source:
            sources.append(source)
            yield source
        raise OSError("synthetic close fault")

    process = Mock(pid=123456, stdin=None)
    launch = Mock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(dialog_executor, "dialog_assignment_input", closing_failure)
    monkeypatch.setattr(dialog_executor.subprocess, "Popen", launch)
    monkeypatch.setattr(dialog_executor.os, "killpg", kill)
    executor = dialog_executor.DialogExecutor(tmp_path / "replay.sqlite", slots=1)
    value = assignment()
    with pytest.raises(OSError, match="synthetic close fault"):
        executor.start(value)
    assert len(sources) == 1 and sources[0].closed
    kill.assert_called_once_with(process.pid, signal.SIGKILL)
    process.wait.assert_called_once_with(timeout=5)
    assert executor.slots.acquire(blocking=False)
    executor.slots.release()
    with pytest.raises(ValueError, match="meet_dialog_replayed"):
        executor.start(value)
    launch.assert_called_once()


def test_maximum_payload_is_bounded_and_launch_failure_closes_input():
    with dialog_assignment_input(b"x" * 16384) as source:
        assert len(source.read()) == 16384
    assert source.closed
    with pytest.raises(OSError, match="synthetic launch fault"):
        with dialog_assignment_input(encode(assignment())) as source:
            raise OSError("synthetic launch fault")
    assert source.closed


@pytest.mark.parametrize("invalid", [False, True])
def test_runtime_closes_inherited_grant_input_before_any_browser_or_hub_client(monkeypatch, invalid):
    from worker.meet_media import dialog_runtime

    stream = io.BytesIO(b"x" * 16385 if invalid else encode(assignment()))
    monkeypatch.setattr(dialog_runtime.sys, "stdin", SimpleNamespace(buffer=stream))
    hub = Mock()

    def create_client(value):
        assert stream.closed
        assert value["meeting"]["grant"].startswith("synthetic-")
        return hub

    factory = Mock(side_effect=create_client)
    run = Mock()
    monkeypatch.setattr(dialog_runtime, "HubDialogClient", factory)
    monkeypatch.setattr(dialog_runtime, "run", run)
    if invalid:
        with pytest.raises(ValueError, match="meet_dialog_payload_invalid"):
            dialog_runtime.main()
        factory.assert_not_called()
        run.assert_not_called()
    else:
        dialog_runtime.main()
        factory.assert_called_once()
        run.assert_called_once()
        hub.call.assert_called_once_with("finish", status="completed")
    assert stream.closed
