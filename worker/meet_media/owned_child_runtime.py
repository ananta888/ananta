"""Exec a trusted helper with kernel-enforced parent-death termination."""

import ctypes
import os
import select
import signal
import sys


def main(arguments):
    if len(arguments) < 2 or not arguments[0].isascii() or not arguments[0].isdigit():
        raise ValueError("meet_owned_child_parent_invalid")
    parent = int(arguments[0])
    if not 1 <= parent <= 2_147_483_647:
        raise ValueError("meet_owned_child_parent_invalid")
    parent_fd = os.pidfd_open(parent)
    if os.getppid() != parent:
        os.close(parent_fd)
        raise ValueError("meet_owned_child_parent_lost")
    if os.getpgrp() != os.getpid():
        os.setsid()
    guardian = os.getpid()
    child = os.fork()  # The bootstrap is single-threaded, unlike its calling service.
    if child == 0:
        os.close(parent_fd)
        expected_parent = guardian
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError("meet_owned_child_parent_guard_failed")
        if os.getppid() != expected_parent:
            raise ValueError("meet_owned_child_parent_lost")
        os.execvp(arguments[1], arguments[1:])
    try:
        while True:
            finished, status = os.waitpid(child, os.WNOHANG)
            if finished:
                code = os.waitstatus_to_exitcode(status)
                return code if code >= 0 else 128 - code
            if select.select([parent_fd], [], [], .1)[0]:
                os.killpg(os.getpgrp(), signal.SIGKILL)
    finally:
        os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
