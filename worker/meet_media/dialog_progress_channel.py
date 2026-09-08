"""Private fixed-size process progress channel; never carries identity or content."""

import os
import re
import socket
import stat
import struct
import time
from contextlib import contextmanager

from worker.meet_media.dialog_progress_budget import instant

PROGRESS_FD = "ANANTA_MEET_CONTROL_PROGRESS_FD"


class DialogProgressChannel:
    def __init__(self):
        self.reader, self.writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.reader.setblocking(False)
            self.writer.setblocking(False)
        except Exception:
            self.close()
            raise

    def child_options(self):
        return {"pass_fds": (self.writer.fileno(),), "env": os.environ | {PROGRESS_FD: str(self.writer.fileno())}}

    def spawned(self):
        self.writer.close()

    def consume(self, budget, clock=time.monotonic):
        for _ in range(16):
            try:
                raw = self.reader.recv(9)
            except BlockingIOError:
                return
            if len(raw) != 8:
                raise ValueError("meet_dialog_progress_packet_invalid")
            budget.observe(struct.unpack("!d", raw)[0], clock())
        try:
            self.reader.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return
        raise ValueError("meet_dialog_progress_flood")

    def close(self):
        try:
            self.reader.close()
        finally:
            self.writer.close()


class DialogProgressSender:
    def __init__(self, channel, *, clock=time.monotonic):
        self.channel, self.clock = channel, clock

    def report(self, fresh_until, assignment_deadline):
        if not instant(fresh_until) or not instant(assignment_deadline):
            raise ValueError("meet_dialog_progress_invalid")
        deadline, now = min(fresh_until, assignment_deadline), self.clock()
        if not instant(now) or not now < deadline <= now + 2.5:
            raise ValueError("meet_dialog_progress_invalid")
        if self.channel.send(struct.pack("!d", deadline)) != 8:
            raise ValueError("meet_dialog_progress_send_failed")


@contextmanager
def inherited_progress():
    raw = os.environ.pop(PROGRESS_FD, None)
    if raw is None:
        yield None  # Direct legacy runtime; installed executors always provide a channel.
        return
    if not re.fullmatch(r"[1-9][0-9]{0,6}", raw) or int(raw) < 3:
        raise ValueError("meet_dialog_progress_descriptor_invalid")
    descriptor = int(raw)
    if not stat.S_ISSOCK(os.fstat(descriptor).st_mode):
        raise ValueError("meet_dialog_progress_descriptor_invalid")
    with socket.socket(fileno=descriptor) as channel:
        os.set_inheritable(descriptor, False)
        if (
            channel.family != socket.AF_UNIX
            or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_DGRAM
        ):
            raise ValueError("meet_dialog_progress_descriptor_invalid")
        channel.setblocking(False)
        yield DialogProgressSender(channel)
