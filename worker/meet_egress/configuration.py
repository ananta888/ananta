"""Bounded, non-following admission of the operator-owned local policy file."""

import os
import stat

from ananta_contracts.meet_egress import MAX_BYTES, parse_egress_policy


def read_policy(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_BYTES:
            raise ValueError("meet_egress_configuration_invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_BYTES + 1)
        return parse_egress_policy(raw)
    finally:
        os.close(descriptor)
