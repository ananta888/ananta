"""Parent failure cleanup retains the ordinary slot and replay invariants."""

from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_transport import assignment
from worker.meet_media.dialog_executor import DialogExecutor
from worker.meet_media.dialog_progress_channel import DialogProgressChannel


def test_descriptor_close_failure_cannot_leak_existing_execution_slot(tmp_path):
    executor = DialogExecutor(tmp_path / "replay.sqlite", slots=1)
    assert executor.slots.acquire(blocking=False)
    progress, process = Mock(), Mock()
    progress.close.side_effect = OSError("synthetic-close-failure")
    process.poll.return_value = 0
    with pytest.raises(OSError, match="synthetic-close-failure"):
        executor._watch(process, progress, Mock())
    assert executor.slots.acquire(blocking=False)
    executor.slots.release()


def test_channel_creation_failure_keeps_replay_fence_and_releases_slot(tmp_path, monkeypatch):
    executor = DialogExecutor(tmp_path / "replay.sqlite", slots=1)
    create = Mock(side_effect=OSError("synthetic-channel-failure"))
    spawn = Mock()
    monkeypatch.setattr("worker.meet_media.dialog_executor.DialogProgressChannel", create)
    monkeypatch.setattr("worker.meet_media.dialog_executor.subprocess.Popen", spawn)
    value = assignment()
    with pytest.raises(OSError, match="synthetic-channel-failure"):
        executor.start(value)
    assert executor.slots.acquire(blocking=False)
    executor.slots.release()
    with pytest.raises(ValueError, match="replayed"):
        executor.start(value)
    spawn.assert_not_called()
    create.assert_called_once()


def test_reader_close_failure_still_closes_writer():
    channel = object.__new__(DialogProgressChannel)
    channel.reader, channel.writer = Mock(), Mock()
    channel.reader.close.side_effect = OSError("synthetic-close-failure")
    with pytest.raises(OSError):
        channel.close()
    channel.writer.close.assert_called_once()
