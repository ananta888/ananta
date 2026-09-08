"""Bounded local private-key admission; no grants, policy decisions or Worker I/O."""

import os
import stat

MAX_KEY_BYTES = 4096


def _validate_file(metadata, require_private=True):
    if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_KEY_BYTES:
        raise ValueError("meet_machine_key_file_invalid")
    if metadata.st_mode & (0o077 if require_private else 0o022):
        raise ValueError("meet_machine_key_permissions")


def _identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def read_meet_key_file(path, *, dir_fd=None, require_private=True, follow_symlinks=True):
    try:
        path = os.fspath(path)
        if b"\0" in os.fsencode(path):
            raise OSError("invalid secret path")
        options = {} if dir_fd is None else {"dir_fd": dir_fd}
        expected = os.stat(path, **options, follow_symlinks=follow_symlinks)
        _validate_file(expected, require_private)
        # Nonblocking also covers a regular-file -> FIFO race between stat/open.
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
        if not follow_symlinks:
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, **options)
        try:
            opened = os.fstat(descriptor)
            _validate_file(opened, require_private)
            if _identity(expected) != _identity(opened):
                raise ValueError("meet_machine_key_changed")
            value = os.read(descriptor, MAX_KEY_BYTES + 1)
            current = os.fstat(descriptor)
            if _identity(opened) != _identity(current) or len(value) != opened.st_size:
                raise ValueError("meet_machine_key_changed")
            return value
        finally:
            os.close(descriptor)
    except (OSError, TypeError):
        raise ValueError("meet_machine_key_unavailable") from None


def load_meet_signing_key(path, *, dir_fd=None, follow_symlinks=True):
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = read_meet_key_file(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    try:
        key = load_pem_private_key(raw, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError("meet_machine_key_invalid") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("meet_machine_key_type_invalid")
    return key
