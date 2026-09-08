"""Bounded local operator document input; no policy storage or activation."""

import os
import stat
from pathlib import Path

from agent.models.meet_preauthorization_policy import MeetPreauthorizationPolicy
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import parse


def _identity(metadata):
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= 4096
    ):
        raise MeetError("meet_preauthorization_input_invalid")
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def read_operator_policy(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise MeetError("meet_preauthorization_input_invalid")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        expected = _identity(os.fstat(descriptor))
        raw = os.read(descriptor, 4097)
        if (
            len(raw) != expected[2]
            or _identity(os.fstat(descriptor)) != expected
            or _identity(path.stat(follow_symlinks=False)) != expected
        ):
            raise MeetError("meet_preauthorization_input_changed")
        return MeetPreauthorizationPolicy.parse(parse(raw)).document()
    finally:
        os.close(descriptor)
