"""Exclusive local provisioning file operations, never an issuer or grant policy."""

import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


def _directory_identity(metadata):
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.geteuid():
        raise ValueError("meet_machine_key_directory_invalid")
    return metadata.st_dev, metadata.st_ino


@contextmanager
def private_key_directory(directory):
    directory = Path(directory)
    if not directory.is_absolute() or directory == Path("/") or directory.resolve() != directory:
        raise ValueError("meet_machine_key_directory_invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    expected = _directory_identity(directory.stat())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if _directory_identity(os.fstat(descriptor)) != expected:
            raise ValueError("meet_machine_key_directory_changed")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("meet_machine_key_provisioning_busy") from None
        yield descriptor
        if _directory_identity(directory.stat()) != expected:
            raise ValueError("meet_machine_key_directory_changed")
    finally:
        os.close(descriptor)


def key_file_exists(directory_fd, name):
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def install_key_file(directory_fd, name, content):
    """Publish a complete inode without overwriting any existing target name."""
    temporary = ".machine-key-install-" + secrets.token_hex(16)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd
    )
    try:
        try:
            if os.write(descriptor, content) != len(content):
                raise ValueError("meet_machine_key_install_incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
        os.fsync(directory_fd)
    finally:
        # Only this invocation's successfully created, exclusive temporary name.
        os.unlink(temporary, dir_fd=directory_fd)
