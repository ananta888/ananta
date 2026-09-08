"""Real private datagrams and descriptor ownership, without browser or Hub policy."""

import os
import socket
import struct
from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_progress_budget import DialogProgressBudget
from worker.meet_media.dialog_progress_channel import (
    PROGRESS_FD,
    DialogProgressChannel,
    DialogProgressSender,
    inherited_progress,
)


def test_fixed_packet_contains_only_absolute_expiry_and_queueing_does_not_refresh_it():
    channel = DialogProgressChannel()
    try:
        sender = DialogProgressSender(channel.writer, clock=lambda: 100)
        sender.report(102.5, 101.5)
        assert channel.reader.recv(9) == struct.pack("!d", 101.5)
        sender.report(102.5, 500)
        budget = DialogProgressBudget(500, 100)
        channel.consume(budget, clock=lambda: 102)
        assert budget.deadline == 103
        channel.consume(budget, clock=lambda: 102.1)
        assert budget.deadline == 103
    finally:
        channel.close()


@pytest.mark.parametrize("packet", [b"", b"x", b"x" * 9, struct.pack("!d", float("nan")), struct.pack("!d", 99)])
def test_malformed_or_expired_datagram_never_counts_as_progress(packet):
    channel = DialogProgressChannel()
    try:
        channel.writer.send(packet)
        budget = DialogProgressBudget(500, 100)
        with pytest.raises(ValueError):
            channel.consume(budget, clock=lambda: 100)
        assert budget.last_progress is None
    finally:
        channel.close()


def test_flood_has_a_fixed_per_tick_read_bound():
    channel = DialogProgressChannel()
    try:
        for _ in range(17):
            channel.writer.send(struct.pack("!d", 102))
        with pytest.raises(ValueError, match="progress_flood"):
            channel.consume(DialogProgressBudget(500, 100), clock=lambda: 100)
    finally:
        channel.close()


def test_inherited_sender_consumes_environment_and_prevents_further_inheritance(monkeypatch):
    channel = DialogProgressChannel()
    descriptor = os.dup(channel.writer.fileno())
    os.set_inheritable(descriptor, True)
    monkeypatch.setenv(PROGRESS_FD, str(descriptor))
    try:
        with inherited_progress() as sender:
            assert PROGRESS_FD not in os.environ and not os.get_inheritable(descriptor)
            assert sender.channel.gettimeout() == 0
        with pytest.raises(OSError):
            os.fstat(descriptor)
        with inherited_progress() as sender:
            assert sender is None
    finally:
        channel.close()


@pytest.mark.parametrize("value", ["", "0", "1", "2", "-1", "03", "private", "9" * 8])
def test_invalid_environment_does_not_open_a_descriptor(monkeypatch, value):
    monkeypatch.setenv(PROGRESS_FD, value)
    with pytest.raises(ValueError, match="descriptor_invalid"):
        with inherited_progress():
            pytest.fail("invalid descriptor admitted")


def test_stream_socket_is_not_substitutable_for_fixed_datagram_channel(monkeypatch):
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    descriptor = writer.detach()
    monkeypatch.setenv(PROGRESS_FD, str(descriptor))
    try:
        with pytest.raises(ValueError, match="descriptor_invalid"):
            with inherited_progress():
                pytest.fail("stream descriptor admitted")
        with pytest.raises(OSError):
            os.fstat(descriptor)
    finally:
        reader.close()


@pytest.mark.parametrize("deadline", [None, True, "102", float("nan"), 99, 100, 103])
def test_sender_never_writes_invalid_or_future_progress(deadline):
    channel = Mock()
    with pytest.raises(ValueError, match="progress_invalid"):
        DialogProgressSender(channel, clock=lambda: 100).report(deadline, 500)
    channel.send.assert_not_called()


def test_spawn_options_inherit_only_writer_and_parent_closes_both_resources():
    channel = DialogProgressChannel()
    reader, writer = channel.reader.fileno(), channel.writer.fileno()
    assert not os.get_inheritable(reader) and not os.get_inheritable(writer)
    options = channel.child_options()
    assert options["pass_fds"] == (writer,) and options["env"][PROGRESS_FD] == str(writer)
    channel.spawned()
    with pytest.raises(OSError):
        os.fstat(writer)
    channel.close()
    channel.close()
    with pytest.raises(OSError):
        os.fstat(reader)


def test_lost_parent_channel_cannot_be_treated_as_successful_progress():
    channel = DialogProgressChannel()
    try:
        channel.reader.close()
        with pytest.raises(OSError):
            DialogProgressSender(channel.writer, clock=lambda: 100).report(102, 500)
    finally:
        channel.close()


def test_short_send_is_terminal_not_a_partial_success():
    channel = Mock()
    channel.send.return_value = 4
    with pytest.raises(ValueError, match="send_failed"):
        DialogProgressSender(channel, clock=lambda: 100).report(102, 500)
    channel.send.assert_called_once()
